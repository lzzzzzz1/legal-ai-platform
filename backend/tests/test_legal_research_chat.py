from app.schemas.review import IntakeChatMessage, LegalResearchRequest
from app.services.legal_research_chat import continue_legal_research_chat


def test_legal_research_fallback_is_explicit_about_unverified_law(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    request = LegalResearchRequest(
        messages=[IntakeChatMessage(role="user", content="民法典中关于违约金的规定是什么？")],
    )

    response = continue_legal_research_chat(request)

    assert response.source == "fallback"
    assert "无法可靠查询并核验" in response.assistant_message
    assert response.warning
    assert len(response.suggested_questions) == 2


def test_legal_research_model_answer_keeps_contract_context_optional(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr("app.services.legal_research_chat.OpenAI", lambda **kwargs: object())
    seen_context: list[str] = []

    def fake_answer(client, request):
        seen_context.append(request.contract_context)
        return type("Result", (), {
            "assistant_message": "这是通用法规信息，请结合现行文本核验。",
            "suggested_questions": ["这项规定如何落入合同条款？"],
            "source": "model",
            "warning": None,
        })()

    monkeypatch.setattr("app.services.legal_research_chat._request_model_answer", fake_answer)
    request = LegalResearchRequest(
        messages=[IntakeChatMessage(role="user", content="数据安全法对合同有什么提示？")],
        contract_context="乙方处理甲方业务数据。",
    )

    response = continue_legal_research_chat(request)

    assert response.source == "model"
    assert seen_context == ["乙方处理甲方业务数据。"]
