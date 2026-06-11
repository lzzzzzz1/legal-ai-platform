from io import BytesIO

from docx import Document

from app.services.docx_parser import extract_docx_text


def _build_docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("合同标题")
    document.add_paragraph("  ")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "甲方联系人"
    table.rows[0].cells[1].text = "张三"

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_extract_docx_text_includes_paragraphs_and_tables() -> None:
    text = extract_docx_text(_build_docx_bytes())

    assert "合同标题" in text
    assert "甲方联系人 | 张三" in text
