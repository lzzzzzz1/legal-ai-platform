import json
import re
from datetime import datetime, timezone
from io import BytesIO
from copy import deepcopy
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree


Modification = dict[str, Any]
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
CLAUSE_PREFIX_RE = re.compile(
    r"^\s*(?:section\s+|article\s+)?"
    r"(?:\(?[0-9]+\)?|"
    r"\(?[a-zA-Z]\)|"
    r"[ivxlcdmIVXLCDM]+[.)]?|"
    r"[0-9]+(?:\.[0-9]+)*[.)]?)\s*[:.)-]*\s*"
)
NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_modifications(modifications_json: str) -> list[Modification]:
    payload = json.loads(modifications_json)
    if not isinstance(payload, list):
        raise ValueError("modifications must be a JSON array")

    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each modification must be an object")

    return payload


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


MISSING_SENTINEL = "\u3010\u7f3a\u5931\u8be5\u7ea6\u5b9a\u3011"


def _is_missing_sentinel(text: str) -> bool:
    return text.strip() in (MISSING_SENTINEL, "\u7f3a\u5931\u8be5\u7ea6\u5b9a")


def _is_story_xml(path: str) -> bool:
    return path == "word/document.xml" or (
        path.endswith(".xml") and any(path.startswith(prefix) for prefix in XML_STORY_PREFIXES[1:])
    )


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


def _replace_paragraph_fully_with_revision(
    paragraph: etree._Element, deleted_text: str, modified_text: str, revision_id: int
) -> None:
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_revision_container(W_DEL, deleted_text, revision_id))
    paragraph.append(_make_revision_container(W_INS, modified_text, revision_id + 1))


def _mark_inserted_paragraph(paragraph: etree._Element, modified: str, revision_id: int) -> None:
    _clear_paragraph_runs(paragraph)
    paragraph.append(_make_revision_container(W_INS, modified, revision_id))


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


def _edit_distance(left: str, right: str) -> int:
    source = left.lower()
    target = right.lower()
    costs = list(range(len(target) + 1))

    for source_index, source_char in enumerate(source, start=1):
        previous_diagonal = source_index - 1
        costs[0] = source_index
        for target_index, target_char in enumerate(target, start=1):
            insertion_cost = costs[target_index] + 1
            deletion_cost = costs[target_index - 1] + 1
            substitution_cost = previous_diagonal + (source_char != target_char)
            previous_diagonal = costs[target_index]
            costs[target_index] = min(insertion_cost, deletion_cost, substitution_cost)

    return costs[-1]


def _similarity(left: str, right: str) -> float:
    longer = left if len(left) >= len(right) else right
    shorter = right if longer is left else left
    if not longer:
        return 1.0
    return (len(longer) - _edit_distance(longer, shorter)) / len(longer)


def _normalize_match_text(text: str) -> str:
    normalized = NON_WORD_RE.sub(" ", text.lower()).strip()
    return " ".join(normalized.split())


def _strip_clause_prefix(text: str) -> str:
    return CLAUSE_PREFIX_RE.sub("", text.strip(), count=1).strip()


def _extract_heading_candidate(text: str) -> str:
    stripped = _strip_clause_prefix(text)
    if not stripped:
        return ""

    parts = re.split(r"[.:\n;。；：]", stripped, maxsplit=1)
    heading = parts[0].strip()
    return heading if len(heading) <= 80 else ""


def _paragraph_match_score(paragraph_text: str, query: str) -> float:
    paragraph_full = _normalize_match_text(paragraph_text)
    query_full = _normalize_match_text(query)
    if not paragraph_full or not query_full:
        return 0.0

    if paragraph_full == query_full:
        return 1.0
    if query_full in paragraph_full:
        return 0.97

    full_similarity = _similarity(paragraph_full, query_full)
    heading_similarity = 0.0

    paragraph_heading = _normalize_match_text(_extract_heading_candidate(paragraph_text))
    query_heading = _normalize_match_text(_extract_heading_candidate(query))
    query_heading = query_heading or query_full

    if paragraph_heading:
        if paragraph_heading == query_heading:
            heading_similarity = 0.96
        elif query_heading in paragraph_heading or paragraph_heading in query_heading:
            heading_similarity = 0.93
        else:
            heading_similarity = _similarity(paragraph_heading, query_heading)

    return max(full_similarity, heading_similarity)


def _find_best_paragraph(root: etree._Element, query: str, threshold: float) -> etree._Element | None:
    best_match = None
    best_score = 0.0

    for paragraph in root.iter(W_P):
        paragraph_text = _paragraph_text(paragraph).strip()
        if not paragraph_text:
            continue
        score = _paragraph_match_score(paragraph_text, query.strip())
        if score > best_score:
            best_score = score
            best_match = paragraph

    if best_score >= threshold:
        return best_match
    return None


def _replace_ooxml_paragraph(paragraph: etree._Element, original: str, modified: str, revision_id: int) -> bool:
    return _replace_with_revision(paragraph, original, modified, revision_id)


def _fuzzy_replace_ooxml_paragraph(root: etree._Element, original: str, modified: str, revision_id: int) -> bool:
    best_match = _find_best_paragraph(root, original, threshold=0.72)
    if best_match is None:
        return False

    _replace_paragraph_fully_with_revision(best_match, _paragraph_text(best_match), modified, revision_id)
    return True


def _insert_after_ooxml_paragraph(root: etree._Element, anchor: str, modified: str, revision_id: int) -> bool:
    for paragraph in root.iter(W_P):
        paragraph_text = _paragraph_text(paragraph)
        if anchor not in paragraph_text:
            continue

        clone = deepcopy(paragraph)
        _mark_inserted_paragraph(clone, modified, revision_id)
        _disable_numbering(clone)
        parent = paragraph.getparent()
        if parent is None:
            return False
        parent.insert(parent.index(paragraph) + 1, clone)
        return True

    best_match = _find_best_paragraph(root, anchor, threshold=0.72)
    if best_match is None:
        return False

    clone = deepcopy(best_match)
    _mark_inserted_paragraph(clone, modified, revision_id)
    _disable_numbering(clone)
    parent = best_match.getparent()
    if parent is None:
        return False
    parent.insert(parent.index(best_match) + 1, clone)
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


def _ensure_track_revisions(settings_xml: bytes) -> bytes:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(settings_xml, parser=parser)
    if root.find(f".//{{{W_NS}}}trackRevisions") is None:
        root.append(etree.Element(f"{{{W_NS}}}trackRevisions"))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _modify_xml_story(
    xml_bytes: bytes, modifications: list[tuple[str, str, str | None]], starting_revision_id: int
) -> tuple[bytes, int, int]:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)
    applied = 0
    revision_id = starting_revision_id

    for original, modified, anchor in modifications:
        if _is_missing_sentinel(original):
            inserted = False
            if anchor:
                inserted = _insert_after_ooxml_paragraph(root, anchor, modified, revision_id)
            if not inserted:
                inserted = _append_ooxml_paragraph(root, modified, revision_id)
            applied += int(inserted)
            if inserted:
                revision_id += 1
            continue

        matched = False
        for paragraph in root.iter(W_P):
            matched = _replace_ooxml_paragraph(paragraph, original, modified, revision_id) or matched
        if not matched:
            matched = _fuzzy_replace_ooxml_paragraph(root, original, modified, revision_id)
        applied += int(matched)
        if matched:
            revision_id += 2

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), applied, revision_id


def modify_docx_inplace(file_bytes: bytes, modifications: list[Modification]) -> bytes:
    normalized_modifications = [
        (*_modification_texts(item), _modification_anchor(item)) for item in modifications
    ]

    output = BytesIO()
    total_applied = 0
    revision_id = 1
    with ZipFile(BytesIO(file_bytes), "r") as source_docx:
        with ZipFile(output, "w", ZIP_DEFLATED) as target_docx:
            for item in source_docx.infolist():
                data = source_docx.read(item.filename)
                if _is_story_xml(item.filename):
                    data, applied, revision_id = _modify_xml_story(data, normalized_modifications, revision_id)
                    total_applied += applied
                elif item.filename == "word/settings.xml":
                    data = _ensure_track_revisions(data)
                target_docx.writestr(item, data)

    if normalized_modifications and total_applied == 0:
        raise ValueError("No matching text or insertion anchor was found in the document.")

    return output.getvalue()
