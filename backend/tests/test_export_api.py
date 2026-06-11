import json
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _build_docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("合同份数：一式两份。")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "签订地点：未约定。"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_export_returns_modified_docx() -> None:
    modifications = [
        {
            "original": "合同份数：一式两份。",
            "modified": "合同份数：一式四份，甲乙双方各执两份。",
        },
        {
            "original": "签订地点：未约定。",
            "modified": "签订地点：上海市浦东新区。",
        },
    ]

    response = client.post(
        "/api/export",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"modifications": json.dumps(modifications, ensure_ascii=False)},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="reviewed_contract.docx"'

    reviewed_document = Document(BytesIO(response.content))
    paragraphs_text = "\n".join(paragraph.text for paragraph in reviewed_document.paragraphs)
    table_text = reviewed_document.tables[0].cell(0, 0).text

    assert "合同份数：一式四份，甲乙双方各执两份。" in paragraphs_text
    assert "合同份数：一式两份。" not in paragraphs_text
    assert "签订地点：上海市浦东新区。" in table_text


def test_export_rejects_invalid_modifications_json() -> None:
    response = client.post(
        "/api/export",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"modifications": "{}"},
    )

    assert response.status_code == 400
