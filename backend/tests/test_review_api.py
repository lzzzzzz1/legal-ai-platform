from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.review import ContractOverview, IntakeChatResponse, ReviewResponse


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
    assert response.json()["status"] == "ok"
    assert response.json()["api_version"] == "2026.08.18-chat-intake"


def test_system_status_does_not_expose_credentials(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-value")
    monkeypatch.setenv("BAILIAN_MODEL", "Qwen-Test")
    monkeypatch.setenv("BAILIAN_BASE_URL", "http://model.internal/v1")
    response = client.get("/api/system-status")

    assert response.status_code == 200
    assert response.json()["review_model"]["model"] == "Qwen-Test"
    assert "secret-value" not in response.text


def test_review_requires_docx() -> None:
    response = client.post(
        "/api/review",
        files={"file": ("contract.txt", b"text", "text/plain")},
    )

    assert response.status_code == 400


def test_overview_returns_contract_orientation(monkeypatch) -> None:
    def fake_create_contract_overview(contract_text: str) -> ContractOverview:
        assert "合同份数" in contract_text
        return ContractOverview(
            contract_type="软件服务合同",
            summary="甲方向乙方采购软件实施服务。",
            parties=["甲方", "乙方"],
            transaction_subject="软件实施服务",
            key_terms=["服务范围待确认"],
            clarification_questions=["请确认我方身份。"],
            method="model",
        )

    monkeypatch.setattr("app.main.create_contract_overview", fake_create_contract_overview)
    response = client.post(
        "/api/overview",
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["contract_type"] == "软件服务合同"
    assert payload["overview"]["parties"] == ["甲方", "乙方"]
    assert payload["overview"]["method"] == "model"
    assert "合同份数" in payload["contract_text"]


def test_intake_chat_returns_modelled_review_criteria(monkeypatch) -> None:
    def fake_continue(request):
        assert request.overview.contract_type == "软件服务合同"
        assert "甲方" in request.contract_text
        return IntakeChatResponse(
            assistant_message="请说明本次交易最想达成的业务结果。",
            criteria={"party_role": "party_a", "business_context": "采购方希望按期上线"},
            ready_for_review=True,
            source="model",
        )

    monkeypatch.setattr("app.main.continue_intake_chat", fake_continue)
    response = client.post(
        "/api/intake/chat",
        json={
            "contract_text": "甲方采购乙方软件服务。",
            "overview": {"contract_type": "软件服务合同", "summary": "甲方采购服务。"},
            "messages": [{"role": "user", "content": "我是甲方采购方。"}],
            "criteria": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["criteria"]["party_role"] == "party_a"
    assert response.json()["source"] == "model"


def test_review_returns_structured_payload(monkeypatch) -> None:
    def fake_review_contract_text(contract_text: str, filename: str) -> ReviewResponse:
        assert "合同份数" in contract_text
        return ReviewResponse(
            filename=filename,
            risks=[
                {
                    "item": "合同份数",
                    "level": "low",
                    "original_text": "合同份数：一式两份。",
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
    assert "合同份数" in response.json()["contract_text"]
    assert response.json()["risks"][0]["item"] == "合同份数"


def test_review_rejects_empty_or_unknown_review_scope() -> None:
    for scope in ("[]", '["未知范围"]', "{}"):
        response = client.post(
            "/api/review",
            data={"review_scope": scope},
            files={
                "file": (
                    "contract.docx",
                    _build_docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 400


def test_review_accepts_selected_scope_and_forwards_it(monkeypatch) -> None:
    captured = {}

    def fake_review(contract_text: str, filename: str, selected_scope: list[str]) -> ReviewResponse:
        captured["scope"] = selected_scope
        return ReviewResponse(filename=filename, risks=[])

    monkeypatch.setattr("app.main.review_contract_text", fake_review)
    response = client.post(
        "/api/review",
        data={"review_scope": '["付款与发票"]'},
        files={
            "file": (
                "contract.docx",
                _build_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    assert captured["scope"] == ["付款与发票"]


def test_text_review_accepts_preflight_corrected_text(monkeypatch) -> None:
    captured = {}

    def fake_review(contract_text: str, filename: str, selected_scope: list[str]) -> ReviewResponse:
        captured.update({"text": contract_text, "filename": filename, "scope": selected_scope})
        return ReviewResponse(filename=filename, risks=[], contract_text=contract_text)

    monkeypatch.setattr("app.main.review_contract_text", fake_review)
    response = client.post(
        "/api/review/text",
        json={
            "filename": "contract.docx",
            "contract_text": "第一条 服务范围，验收。",
            "review_scope": ["付款与发票"],
        },
    )

    assert response.status_code == 200
    assert captured == {
        "text": "第一条 服务范围，验收。",
        "filename": "contract.docx",
        "scope": ["付款与发票"],
    }
