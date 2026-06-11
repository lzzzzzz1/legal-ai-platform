from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.review import ReviewResponse


client = TestClient(app)


def _build_docx_bytes(text: str = "合同份数：一式两份。") -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_review_requires_docx() -> None:
    response = client.post(
        "/api/review",
        files={"file": ("contract.txt", b"text", "text/plain")},
    )

    assert response.status_code == 400


def test_review_returns_structured_payload(monkeypatch) -> None:
    def fake_review_contract_text(contract_text: str, filename: str) -> ReviewResponse:
        assert "合同份数" in contract_text
        return ReviewResponse(
            filename=filename,
            risks=[
                {
                    "item": "合同份数",
                    "level": "low",
                    "risk": "合同份数已约定，风险较低。",
                    "suggestion": "保留当前约定并核对盖章份数。",
                    "laws": ["《中华人民共和国民法典》第四百七十条"],
                }
            ],
        )

    monkeypatch.setattr("app.main.review_contract_text", fake_review_contract_text)

    response = client.post(
        "/api/review",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["risks"][0]["item"] == "合同份数"
