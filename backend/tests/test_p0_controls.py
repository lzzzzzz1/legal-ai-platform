from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.review import ReviewResponse


client = TestClient(app)


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("contract text")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_review_requires_configured_api_token(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "test-api-token")

    response = client.post(
        "/api/review",
        files={"file": ("contract.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 401


def test_review_accepts_configured_api_token_and_valid_tenant(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "test-api-token")
    monkeypatch.setattr(
        "app.main.review_contract_text",
        lambda contract_text, filename: ReviewResponse(filename=filename, risks=[]),
    )

    response = client.post(
        "/api/review",
        headers={"X-API-Token": "test-api-token", "X-Tenant-ID": "acme"},
        files={"file": ("contract.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 200


def test_review_rejects_invalid_tenant_id(monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_TOKEN", "test-api-token")

    response = client.post(
        "/api/review",
        headers={"X-API-Token": "test-api-token", "X-Tenant-ID": "../other-tenant"},
        files={"file": ("contract.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 400
