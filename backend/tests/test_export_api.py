import json
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _build_docx_bytes() -> bytes:
    document = Document()
    styled_paragraph = document.add_paragraph("合同份数：一式两份。")
    styled_paragraph.style = "List Paragraph"
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


def test_export_fuzzy_matches_replacement_text() -> None:
    modifications = [
        {
            "original": "合同份数一式两份",
            "modified": "合同份数：一式三份，甲乙双方各执一份，存档一份。",
        }
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

    reviewed_document = Document(BytesIO(response.content))
    paragraphs_text = "\n".join(paragraph.text for paragraph in reviewed_document.paragraphs)

    assert "合同份数：一式三份，甲乙双方各执一份，存档一份。" in paragraphs_text


def test_export_inserts_missing_clause_after_anchor() -> None:
    response = client.post(
        "/api/export",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "modifications": json.dumps(
                [
                    {
                        "original": "【缺失该约定】",
                        "modified": "新增税务条款：税费由乙方承担。",
                        "insert_after_text": "合同份数：一式两份。",
                    }
                ],
                ensure_ascii=False,
            )
        },
    )

    assert response.status_code == 200

    reviewed_document = Document(BytesIO(response.content))
    paragraphs = reviewed_document.paragraphs

    assert paragraphs[0].text == "合同份数：一式两份。"
    assert paragraphs[1].text == "新增税务条款：税费由乙方承担。"
    assert paragraphs[1].style.name == paragraphs[0].style.name


def test_export_fuzzy_matches_insertion_anchor() -> None:
    response = client.post(
        "/api/export",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "modifications": json.dumps(
                [
                    {
                        "original": "【缺失该约定】",
                        "modified": "新增通知条款：双方应明确联系人与送达邮箱。",
                        "insert_after_text": "合同份数一式两份",
                    }
                ],
                ensure_ascii=False,
            )
        },
    )

    assert response.status_code == 200

    reviewed_document = Document(BytesIO(response.content))
    paragraphs = reviewed_document.paragraphs

    assert paragraphs[1].text == "新增通知条款：双方应明确联系人与送达邮箱。"


def test_export_appends_missing_clause_modification() -> None:
    response = client.post(
        "/api/export",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "modifications": json.dumps(
                [{"original": "【缺失该约定】", "modified": "新增条款：双方应明确通知联系人。"}],
                ensure_ascii=False,
            )
        },
    )

    assert response.status_code == 200

    reviewed_document = Document(BytesIO(response.content))
    paragraphs_text = "\n".join(paragraph.text for paragraph in reviewed_document.paragraphs)

    assert "新增条款：双方应明确通知联系人。" in paragraphs_text


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
