from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.review import DeepReviewSettings, ReviewResponse
from app.services import deep_review


def _deep_payload() -> dict:
    return {
        "contract_type": "服务合同",
        "review_summary": "已按我方立场完成深度商业与谈判审查。",
        "risks": [
            {
                "item": "付款与发票",
                "level": "high",
                "original_text": "甲方应在签约后支付全部费用。",
                "clause_reference": "第 3 条",
                "risk": "付款未与交付、验收和发票挂钩。",
                "party_impact": "我方承担预付风险。",
                "suggestion": "甲方应在验收合格并收到合法有效发票后 30 日内支付。",
                "minimum_acceptable_text": "付款至少应以验收和发票为前提。",
                "negotiation_level": "must_modify",
                "laws": [],
            }
        ],
        "coverage": [],
        "deep_review": {
            "state": "completed",
            "overall_conclusion": "有条件可签",
            "executive_summary": "建议先完成付款条件调整，再推进签署。",
            "key_facts": [{"item": "付款", "contract_term": "签约后全额付款", "conclusion": "对我方不利"}],
            "missing_clauses": ["缺少验收标准"],
            "negotiation_items": [{"topic": "付款", "target": "验收后付款", "minimum_acceptable": "验收与发票挂钩", "owner": "法务/财务"}],
            "clarification_questions": ["是否允许预付款？"],
            "settings_note": "已按甲方保护立场审查。",
        },
    }


def test_deep_review_parses_structured_model_result(monkeypatch) -> None:
    class FakeCompletions:
        def create(self, **kwargs):
            assert "response_format" not in kwargs
            assert "party_a" in kwargs["messages"][1]["content"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=deep_review.json.dumps(_deep_payload(), ensure_ascii=False)))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(deep_review, "OpenAI", FakeOpenAI)
    response = deep_review.review_contract_deeply(
        "甲方应在签约后支付全部费用。",
        "contract.docx",
        DeepReviewSettings(party_role="party_a"),
    )

    assert response.deep_review is not None
    assert response.deep_review.state == "completed"
    assert response.risks[0].negotiation_level == "must_modify"
    assert response.manual_review_required is True


def test_deep_review_normalizes_string_coverage(monkeypatch) -> None:
    payload = _deep_payload()
    payload["coverage"] = ["价格与付款", "交付与验收"]

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=deep_review.json.dumps(payload, ensure_ascii=False)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(deep_review, "OpenAI", FakeOpenAI)
    response = deep_review.review_contract_deeply(
        "甲方应在签约后支付全部费用。",
        "contract.docx",
        DeepReviewSettings(party_role="party_a"),
    )

    assert [item.topic for item in response.coverage] == ["价格与付款", "交付与验收"]
    assert all(item.status == "checked" for item in response.coverage)


def test_deep_review_endpoint_forwards_settings(monkeypatch) -> None:
    captured = {}

    def fake_deep_review(contract_text: str, filename: str, settings: DeepReviewSettings) -> ReviewResponse:
        captured.update({"text": contract_text, "filename": filename, "settings": settings})
        payload = _deep_payload()
        return ReviewResponse(filename=filename, contract_text=contract_text, **payload)

    monkeypatch.setattr("app.main.review_contract_deeply", fake_deep_review)
    response = TestClient(app).post(
        "/api/review/deep",
        json={
            "filename": "contract.docx",
            "contract_text": "甲方应在签约后支付全部费用。",
            "settings": {"party_role": "party_a", "review_style": "protective"},
        },
    )

    assert response.status_code == 200
    assert captured["settings"].party_role == "party_a"
    assert response.json()["deep_review"]["overall_conclusion"] == "有条件可签"


def test_other_party_role_requires_description() -> None:
    with pytest.raises(ValueError, match="other_party_role"):
        DeepReviewSettings(party_role="other")


def test_deep_review_request_failure_is_audited_and_controlled(monkeypatch, tmp_path) -> None:
    class BrokenOpenAI:
        def __init__(self, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("REVIEW_AUDIT_LOG", str(tmp_path / "reviews.jsonl"))
    monkeypatch.setattr(deep_review, "OpenAI", BrokenOpenAI)

    with pytest.raises(Exception) as exc_info:
        deep_review.review_contract_deeply(
            "甲方应在签约后支付全部费用。",
            "contract.docx",
            DeepReviewSettings(party_role="party_a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 502
    assert "temporarily unavailable" in str(getattr(exc_info.value, "detail", ""))
    assert "deep_review_request_failed" in (tmp_path / "reviews.jsonl").read_text(encoding="utf-8")


def test_deep_review_rejects_truncated_contract_before_model_call(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    with pytest.raises(Exception) as exc_info:
        deep_review.review_contract_deeply(
            "x" * (deep_review.MAX_CONTRACT_CHARS + 1),
            "contract.docx",
            DeepReviewSettings(party_role="party_a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 413
