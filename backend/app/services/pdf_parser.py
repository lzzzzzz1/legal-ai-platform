import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PdfExtractionResult:
    text: str
    pages: int
    extracted_chars: int
    status: str
    average_chars_per_page: float
    ocr_detected: bool = False


def extract_pdf_text(file_bytes: bytes, filename: str) -> str:
    return extract_pdf_document(file_bytes, filename).text


def extract_pdf_document(file_bytes: bytes, filename: str) -> PdfExtractionResult:
    url = os.getenv("PDF_PARSE_URL", "http://192.168.6.71:30003/v1/pdfparse")
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    timeout = float(os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "300"))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        response = httpx.post(
            url,
            headers=headers,
            files={"file": (filename, file_bytes, "application/pdf")},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"PDF parser returned HTTP {response.status_code}.")
        payload: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"PDF parse request failed: {exc}") from exc

    text = _extract_page_aware_text(payload) if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("PDF parser returned no readable markdown text.")
    pages = _page_count(payload, text)
    extracted_chars = len("".join(text.split()))
    average_chars_per_page = extracted_chars / pages if pages else float(extracted_chars)
    status = _quality_status(extracted_chars, pages, text)
    return PdfExtractionResult(
        text=text.strip(),
        pages=pages,
        extracted_chars=extracted_chars,
        status=status,
        average_chars_per_page=round(average_chars_per_page, 1),
        ocr_detected=_ocr_detected(payload),
    )


def _page_count(payload: dict[str, Any], text: str) -> int:
    pages = payload.get("pages")
    if isinstance(pages, list):
        return max(len(pages), 1)
    if isinstance(pages, int) and pages > 0:
        return pages
    markers = text.count("[PDF第")
    return max(markers, 1)


def _quality_status(extracted_chars: int, pages: int, text: str) -> str:
    if extracted_chars == 0:
        return "scanned"
    average = extracted_chars / max(pages, 1)
    blank_markers = sum(
        1 for chunk in text.split("[PDF第")
        if chunk and len("".join(chunk.split())) < 20
    )
    if average < 80 or (pages > 1 and blank_markers / pages > 0.3):
        return "partial"
    return "searchable"


def _ocr_detected(payload: dict[str, Any]) -> bool:
    for key in ("ocr", "ocr_used", "is_ocr"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    return False


def _extract_page_aware_text(payload: dict[str, Any]) -> str:
    """Preserve page markers when the OCR/parser service returns page objects."""
    pages = payload.get("pages")
    if isinstance(pages, list):
        chunks = []
        for index, page in enumerate(pages, start=1):
            if isinstance(page, str):
                page_text = page
            elif isinstance(page, dict):
                page_text = page.get("markdown") or page.get("text") or page.get("content") or ""
            else:
                page_text = ""
            if isinstance(page_text, str) and page_text.strip():
                chunks.append(f"[PDF第{index}页]\n{page_text.strip()}")
        if chunks:
            return "\n\n".join(chunks)
    return payload.get("markdown") or payload.get("text") or ""
