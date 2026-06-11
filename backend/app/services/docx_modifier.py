import json
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
W_PPR = f"{{{W_NS}}}pPr"
W_NUMPR = f"{{{W_NS}}}numPr"
W_NUMID = f"{{{W_NS}}}numId"
W_VAL = f"{{{W_NS}}}val"
XML_STORY_PREFIXES = ("word/document.xml", "word/header", "word/footer")


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


def _find_best_paragraph(root: etree._Element, query: str, threshold: float) -> etree._Element | None:
    best_match = None
    best_similarity = 0.0

    for paragraph in root.iter(W_P):
        paragraph_text = _paragraph_text(paragraph).strip()
        if not paragraph_text:
            continue
        similarity = _similarity(paragraph_text, query.strip())
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = paragraph

    if best_similarity >= threshold:
        return best_match
    return None


def _replace_ooxml_paragraph(paragraph: etree._Element, original: str, modified: str) -> bool:
    paragraph_text = _paragraph_text(paragraph)
    if original not in paragraph_text:
        return False

    _set_paragraph_text(paragraph, paragraph_text.replace(original, modified))
    return True


def _fuzzy_replace_ooxml_paragraph(root: etree._Element, original: str, modified: str) -> bool:
    best_match = _find_best_paragraph(root, original, threshold=0.72)
    if best_match is None:
        return False

    _set_paragraph_text(best_match, modified)
    return True


def _insert_after_ooxml_paragraph(root: etree._Element, anchor: str, modified: str) -> bool:
    for paragraph in root.iter(W_P):
        paragraph_text = _paragraph_text(paragraph)
        if anchor not in paragraph_text:
            continue

        clone = deepcopy(paragraph)
        _set_paragraph_text(clone, modified)
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
    _set_paragraph_text(clone, modified)
    _disable_numbering(clone)
    parent = best_match.getparent()
    if parent is None:
        return False
    parent.insert(parent.index(best_match) + 1, clone)
    return True

def _append_ooxml_paragraph(root: etree._Element, modified: str) -> bool:
    body = root.find(f".//{{{W_NS}}}body")
    if body is None:
        return False

    paragraphs = list(body.iter(W_P))
    if paragraphs:
        clone = deepcopy(paragraphs[-1])
        _set_paragraph_text(clone, modified)
        _disable_numbering(clone)
    else:
        clone = etree.Element(W_P)
        _set_paragraph_text(clone, modified)

    sect_pr = body.find(f"{{{W_NS}}}sectPr")
    if sect_pr is None:
        body.append(clone)
    else:
        body.insert(body.index(sect_pr), clone)
    return True


def _modify_xml_story(xml_bytes: bytes, modifications: list[tuple[str, str, str | None]]) -> tuple[bytes, int]:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)
    applied = 0

    for original, modified, anchor in modifications:
        if _is_missing_sentinel(original):
            inserted = False
            if anchor:
                inserted = _insert_after_ooxml_paragraph(root, anchor, modified)
            if not inserted:
                inserted = _append_ooxml_paragraph(root, modified)
            applied += int(inserted)
            continue

        matched = False
        for paragraph in root.iter(W_P):
            matched = _replace_ooxml_paragraph(paragraph, original, modified) or matched
        if not matched:
            matched = _fuzzy_replace_ooxml_paragraph(root, original, modified)
        applied += int(matched)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), applied


def modify_docx_inplace(file_bytes: bytes, modifications: list[Modification]) -> bytes:
    normalized_modifications = [
        (*_modification_texts(item), _modification_anchor(item)) for item in modifications
    ]

    output = BytesIO()
    total_applied = 0
    with ZipFile(BytesIO(file_bytes), "r") as source_docx:
        with ZipFile(output, "w", ZIP_DEFLATED) as target_docx:
            for item in source_docx.infolist():
                data = source_docx.read(item.filename)
                if _is_story_xml(item.filename):
                    data, applied = _modify_xml_story(data, normalized_modifications)
                    total_applied += applied
                target_docx.writestr(item, data)

    if normalized_modifications and total_applied == 0:
        raise ValueError("No matching text or insertion anchor was found in the document.")

    return output.getvalue()
