import json
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from copy import deepcopy
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree
from app.services.docx_parser import validate_docx_file_bytes


Modification = dict[str, Any]
MAX_MODIFICATIONS = 200
MAX_MODIFICATION_TEXT_CHARS = 20_000
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{W_NS}}}p"
W_R = f"{{{W_NS}}}r"
W_T = f"{{{W_NS}}}t"
W_DEL_TEXT = f"{{{W_NS}}}delText"
W_PPR = f"{{{W_NS}}}pPr"
W_NUMPR = f"{{{W_NS}}}numPr"
W_NUMID = f"{{{W_NS}}}numId"
W_INS = f"{{{W_NS}}}ins"
W_DEL = f"{{{W_NS}}}del"
W_VAL = f"{{{W_NS}}}val"
W_ID = f"{{{W_NS}}}id"
W_AUTHOR = f"{{{W_NS}}}author"
W_DATE = f"{{{W_NS}}}date"
XML_STORY_PREFIXES = ("word/document.xml", "word/header", "word/footer")


@dataclass(frozen=True)
class DocxExportResult:
    """The export payload and an auditable count of applied revisions."""

    content: bytes
    requested: int
    applied: int

    @property
    def skipped(self) -> int:
        return max(0, self.requested - self.applied)


def parse_modifications(modifications_json: str) -> list[Modification]:
    payload = json.loads(modifications_json)
    if not isinstance(payload, list):
        raise ValueError("modifications must be a JSON array")
    if not payload:
        raise ValueError("at least one modification is required")
    if len(payload) > MAX_MODIFICATIONS:
        raise ValueError(f"at most {MAX_MODIFICATIONS} modifications are allowed")

    seen_originals: dict[str, str] = {}
    unique_modifications: list[Modification] = []
    seen_entries: set[tuple[str, str, str | None]] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each modification must be an object")
        original, modified = _modification_texts(item)
        if len(original) > MAX_MODIFICATION_TEXT_CHARS or len(modified) > MAX_MODIFICATION_TEXT_CHARS:
            raise ValueError("each modification text must be 20000 characters or shorter")
        anchor = _modification_anchor(item)
        paragraph_context = _modification_context(item)
        if paragraph_context and len(paragraph_context) > MAX_MODIFICATION_TEXT_CHARS:
            raise ValueError("each paragraph context must be 20000 characters or shorter")
        entry_key = (original, modified, anchor)
        if entry_key in seen_entries:
            continue
        seen_entries.add(entry_key)

        # Multiple missing clauses legitimately share the sentinel and may be
        # inserted at different anchors.  Only real source text must have one
        # unambiguous replacement.
        if not _is_missing_sentinel(original):
            previous = seen_originals.get(original)
            if previous is not None and previous != modified:
                raise ValueError("the same original text cannot have conflicting replacements")
            seen_originals[original] = modified
        unique_modifications.append(item)

    if not unique_modifications:
        raise ValueError("at least one modification is required")
    return unique_modifications


def _modification_texts(modification: Modification) -> tuple[str, str]:
    original = modification.get("original")
    if original is None:
        original = modification.get("original_text")

    modified = modification.get("modified")
    if modified is None:
        modified = modification.get("suggestion")

    if not isinstance(original, str) or not original:
        raise ValueError("each modification requires a non-empty original text")

    if not isinstance(modified, str) or not modified:
        raise ValueError("each modification requires a non-empty modified text")

    return original, modified


def _modification_anchor(modification: Modification) -> str | None:
    anchor = modification.get("insert_after_text")
    if anchor is None:
        anchor = modification.get("anchor_text")
    if not isinstance(anchor, str) or not anchor.strip():
        return None
    return anchor


def _modification_context(modification: Modification) -> str | None:
    """Return the user-confirmed source paragraph for an ambiguous quote.

    The context is always exact text from the editor.  It narrows replacement
    to one paragraph while preserving a granular Word revision for only the
    quoted words, rather than accepting a fuzzy model match.
    """
    context = modification.get("paragraph_context")
    if not isinstance(context, str) or not context.strip():
        return None
    return context


MISSING_SENTINEL = "\u3010\u7f3a\u5931\u8be5\u7ea6\u5b9a\u3011"


def _is_missing_sentinel(text: str) -> bool:
    return text.strip() in (MISSING_SENTINEL, "\u7f3a\u5931\u8be5\u7ea6\u5b9a")


def _is_story_xml(path: str) -> bool:
    # Contract edits are intentionally limited to the main document body.  A
    # clause-like phrase in a header/footer is often branding, a page label or a
    # legal notice; replacing it is both surprising and legally unsafe.
    return path == "word/document.xml"


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(text_node.text or "" for text_node in paragraph.iter(W_T))


def _set_paragraph_text(paragraph: etree._Element, text: str) -> None:
    text_nodes = list(paragraph.iter(W_T))
    if text_nodes:
        text_nodes[0].text = text
        for node in text_nodes[1:]:
            node.text = ""
        return

    run = etree.SubElement(paragraph, W_R)
    text_node = etree.SubElement(run, W_T)
    text_node.text = text


def _current_revision_date() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_run(text: str, deleted: bool = False) -> etree._Element:
    run = etree.Element(W_R)
    text_node = etree.SubElement(run, W_DEL_TEXT if deleted else W_T)
    text_node.text = text
    if not deleted:
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return run


def _make_revision_container(tag: str, text: str, revision_id: int) -> etree._Element:
    container = etree.Element(tag)
    container.set(W_ID, str(revision_id))
    container.set(W_AUTHOR, "Legal AI")
    container.set(W_DATE, _current_revision_date())
    container.append(_make_run(text, deleted=tag == W_DEL))
    return container


def _clear_paragraph_runs(paragraph: etree._Element) -> None:
    for child in list(paragraph):
        if child.tag != W_PPR:
            paragraph.remove(child)


def _replace_with_revision(paragraph: etree._Element, original: str, modified: str, revision_id: int) -> bool:
    paragraph_text = _paragraph_text(paragraph)
    exact_index = paragraph_text.find(original)
    if exact_index < 0:
        return False

    prefix = paragraph_text[:exact_index]
    suffix = paragraph_text[exact_index + len(original) :]
    _clear_paragraph_runs(paragraph)

    if prefix:
        paragraph.append(_make_run(prefix))
    paragraph.append(_make_revision_container(W_DEL, original, revision_id))
    paragraph.append(_make_revision_container(W_INS, modified, revision_id + 1))
    if suffix:
        paragraph.append(_make_run(suffix))
    return True


def _mark_inserted_paragraph(paragraph: etree._Element, modified: str, revision_id: int) -> None:
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_revision_container(W_INS, modified, revision_id))


def _replace_with_final_text(paragraph: etree._Element, original: str, modified: str) -> bool:
    """Replace one exact occurrence without adding Word revision markup."""
    paragraph_text = _paragraph_text(paragraph)
    exact_index = paragraph_text.find(original)
    if exact_index < 0:
        return False

    prefix = paragraph_text[:exact_index]
    suffix = paragraph_text[exact_index + len(original) :]
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_run(f"{prefix}{modified}{suffix}"))
    return True


def _replace_paragraph_fully_with_final_text(
    paragraph: etree._Element, modified_text: str
) -> None:
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_run(modified_text))


def _disable_numbering(paragraph: etree._Element) -> None:
    ppr = paragraph.find(W_PPR)
    if ppr is None:
        ppr = etree.Element(W_PPR)
        paragraph.insert(0, ppr)

    numpr = ppr.find(W_NUMPR)
    if numpr is None:
        numpr = etree.SubElement(ppr, W_NUMPR)

    numid = numpr.find(W_NUMID)
    if numid is None:
        numid = etree.SubElement(numpr, W_NUMID)
    numid.set(W_VAL, "0")


def _replace_ooxml_paragraph(paragraph: etree._Element, original: str, modified: str, revision_id: int) -> bool:
    return _replace_with_revision(paragraph, original, modified, revision_id)


def _insert_after_ooxml_paragraph(root: etree._Element, anchor: str, modified: str, revision_id: int) -> bool:
    matches = [
        paragraph
        for paragraph in root.iter(W_P)
        for _ in range(_paragraph_text(paragraph).count(anchor))
    ]
    if len(matches) != 1:
        return False

    paragraph = matches[0]
    clone = deepcopy(paragraph)
    _mark_inserted_paragraph(clone, modified, revision_id)
    _disable_numbering(clone)
    parent = paragraph.getparent()
    if parent is None:
        return False
    parent.insert(parent.index(paragraph) + 1, clone)
    return True

def _append_ooxml_paragraph(root: etree._Element, modified: str, revision_id: int) -> bool:
    body = root.find(f".//{{{W_NS}}}body")
    if body is None:
        return False

    paragraphs = list(body.iter(W_P))
    if paragraphs:
        clone = deepcopy(paragraphs[-1])
        _mark_inserted_paragraph(clone, modified, revision_id)
        _disable_numbering(clone)
    else:
        clone = etree.Element(W_P)
        _mark_inserted_paragraph(clone, modified, revision_id)

    sect_pr = body.find(f"{{{W_NS}}}sectPr")
    if sect_pr is None:
        body.append(clone)
    else:
        body.insert(body.index(sect_pr), clone)
    return True


def _insert_final_paragraph_after(root: etree._Element, anchor: str, modified: str) -> bool:
    """Insert a plain paragraph after the selected anchor for a clean export."""
    matches = [
        paragraph
        for paragraph in root.iter(W_P)
        for _ in range(_paragraph_text(paragraph).count(anchor))
    ]
    if len(matches) != 1:
        return False

    paragraph = matches[0]
    clone = deepcopy(paragraph)
    _clear_paragraph_runs(clone)
    clone.append(_make_run(modified))
    _disable_numbering(clone)
    parent = paragraph.getparent()
    if parent is None:
        return False
    parent.insert(parent.index(paragraph) + 1, clone)
    return True


def _append_final_paragraph(root: etree._Element, modified: str) -> bool:
    body = root.find(f".//{{{W_NS}}}body")
    if body is None:
        return False
    clone = etree.Element(W_P)
    _mark_plain_paragraph(clone, modified)
    sect_pr = body.find(f"{{{W_NS}}}sectPr")
    if sect_pr is None:
        body.append(clone)
    else:
        body.insert(body.index(sect_pr), clone)
    return True


def _mark_plain_paragraph(paragraph: etree._Element, text: str) -> None:
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_run(text))


def _ensure_track_revisions(settings_xml: bytes) -> bytes:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(settings_xml, parser=parser)
    if root.find(f".//{{{W_NS}}}trackRevisions") is None:
        root.append(etree.Element(f"{{{W_NS}}}trackRevisions"))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _remove_track_revisions(settings_xml: bytes) -> bytes:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(settings_xml, parser=parser)
    for node in root.findall(f".//{{{W_NS}}}trackRevisions"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _accept_existing_revisions(root: etree._Element) -> None:
    """Flatten revisions already present in an uploaded DOCX for final export."""
    for deleted in list(root.iter(W_DEL)):
        parent = deleted.getparent()
        if parent is not None:
            parent.remove(deleted)

    for inserted in list(root.iter(W_INS)):
        parent = inserted.getparent()
        if parent is None:
            continue
        index = parent.index(inserted)
        for child in list(inserted):
            if child.tag == W_R:
                for text_node in child.iter(W_DEL_TEXT):
                    text_node.tag = W_T
                parent.insert(index, child)
                index += 1
        parent.remove(inserted)


def _replace_many_with_revisions(
    paragraph: etree._Element,
    replacements: list[tuple[int, int, str, str, int]],
) -> int:
    """Write several non-overlapping tracked replacements into one paragraph.

    Applying replacements one at a time used to clear a paragraph's existing
    runs, which silently removed the previous revision when two suggestions
    targeted the same clause.  Rendering them in a single pass retains every
    deletion and insertion for Word's review view.
    """
    paragraph_text = _paragraph_text(paragraph)
    _clear_paragraph_runs(paragraph)
    cursor = 0
    for start, end, original, modified, revision_id in replacements:
        if start > cursor:
            paragraph.append(_make_run(paragraph_text[cursor:start]))
        paragraph.append(_make_revision_container(W_DEL, original, revision_id))
        paragraph.append(_make_revision_container(W_INS, modified, revision_id + 1))
        cursor = end
    if cursor < len(paragraph_text):
        paragraph.append(_make_run(paragraph_text[cursor:]))
    return len(replacements)


def _modify_xml_story(
    xml_bytes: bytes,
    modifications: list[tuple[str, str, str | None, str | None]],
    starting_revision_id: int,
    track_revisions: bool = True,
) -> tuple[bytes, int, int]:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)
    if not track_revisions:
        _accept_existing_revisions(root)
    applied = 0
    revision_id = starting_revision_id

    if track_revisions:
        # Insertions do not rewrite existing text.  Apply them first so a later
        # replacement cannot make an otherwise valid anchor disappear.
        remaining_replacements: list[tuple[str, str, str | None, str | None]] = []
        for original, modified, anchor, paragraph_context in modifications:
            if not _is_missing_sentinel(original):
                remaining_replacements.append((original, modified, anchor, paragraph_context))
                continue
            inserted = _insert_after_ooxml_paragraph(root, anchor, modified, revision_id) if anchor else False
            if not inserted and not anchor:
                inserted = _append_ooxml_paragraph(root, modified, revision_id)
            applied += int(inserted)
            if inserted:
                revision_id += 1

        # Match all exact replacements against each paragraph before changing
        # it. This preserves distinct Word revision records within a clause.
        unmatched = [
            (modification_index, modification)
            for modification_index, modification in enumerate(remaining_replacements)
            if sum(
                _paragraph_text(paragraph).count(modification[0])
                for paragraph in root.iter(W_P)
                if not modification[3] or _paragraph_text(paragraph) == modification[3]
            ) == 1
        ]
        for paragraph in root.iter(W_P):
            if not unmatched:
                break
            paragraph_text = _paragraph_text(paragraph)
            candidates: list[tuple[int, int, int, str, str]] = []
            for modification_index, (original, modified, _anchor, paragraph_context) in unmatched:
                if paragraph_context and paragraph_text != paragraph_context:
                    continue
                start = paragraph_text.find(original)
                if start >= 0:
                    candidates.append((start, start + len(original), modification_index, original, modified))
            if not candidates:
                continue

            selected: list[tuple[int, int, str, str, int]] = []
            selected_indexes: set[int] = set()
            occupied_until = 0
            for start, end, modification_index, original, modified in sorted(candidates, key=lambda item: (item[0], item[1])):
                if start < occupied_until:
                    continue
                selected.append((start, end, original, modified, revision_id + len(selected) * 2))
                selected_indexes.add(modification_index)
                occupied_until = end
            if not selected:
                continue
            applied += _replace_many_with_revisions(paragraph, selected)
            revision_id += len(selected) * 2
            unmatched = [item for item in unmatched if item[0] not in selected_indexes]

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), applied, revision_id

    for original, modified, anchor, paragraph_context in modifications:
        if _is_missing_sentinel(original):
            inserted = False
            if anchor:
                inserted = (
                    _insert_after_ooxml_paragraph(root, anchor, modified, revision_id)
                    if track_revisions
                    else _insert_final_paragraph_after(root, anchor, modified)
                )
            if not inserted and track_revisions:
                inserted = (
                    _append_ooxml_paragraph(root, modified, revision_id)
                )
            applied += int(inserted)
            if inserted:
                revision_id += 1
            continue

        matches = [
            paragraph
            for paragraph in root.iter(W_P)
            if not paragraph_context or _paragraph_text(paragraph) == paragraph_context
            for _ in range(_paragraph_text(paragraph).count(original))
        ]
        matched = False
        if len(matches) == 1:
            paragraph = matches[0]
            if track_revisions:
                matched = _replace_ooxml_paragraph(paragraph, original, modified, revision_id)
            else:
                matched = _replace_with_final_text(paragraph, original, modified)
        applied += int(matched)
        if matched:
            revision_id += 2

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), applied, revision_id


def modify_docx_inplace(
    file_bytes: bytes,
    modifications: list[Modification],
    track_revisions: bool = True,
) -> DocxExportResult:
    validate_docx_file_bytes(file_bytes)
    normalized_modifications = [
        (*_modification_texts(item), _modification_anchor(item), _modification_context(item)) for item in modifications
    ]

    output = BytesIO()
    total_applied = 0
    revision_id = 1
    with ZipFile(BytesIO(file_bytes), "r") as source_docx:
        with ZipFile(output, "w", ZIP_DEFLATED) as target_docx:
            for item in source_docx.infolist():
                data = source_docx.read(item.filename)
                if _is_story_xml(item.filename):
                    data, applied, revision_id = _modify_xml_story(
                        data, normalized_modifications, revision_id, track_revisions=track_revisions
                    )
                    total_applied += applied
                elif item.filename == "word/settings.xml":
                    data = _ensure_track_revisions(data) if track_revisions else _remove_track_revisions(data)
                target_docx.writestr(item, data)

    if normalized_modifications and total_applied == 0:
        raise ValueError(
            "One or more modifications could not be located exactly in the document. "
            "Please re-locate the clause in the editor before exporting the final contract."
        )
    # A reviewer can intentionally leave a suggestion pending.  It must not
    # prevent export of other, precisely located revisions.  The caller gets
    # the count in response headers so the UI can state this explicitly.
    return DocxExportResult(
        content=output.getvalue(),
        requested=len(normalized_modifications),
        applied=total_applied,
    )
