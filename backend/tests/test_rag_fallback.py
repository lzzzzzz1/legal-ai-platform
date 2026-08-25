from app.services import openai_review
from app.services.openai_review import review_contract_text


def test_review_continues_when_law_retrieval_is_unavailable(monkeypatch) -> None:
    class FakeCompletions:
        def create(self, **kwargs):
            user_prompt = kwargs["messages"][1]["content"]
            assert "未检索到可用法条" in user_prompt
            return type(
                "Response",
                (),
                {"choices": [type("Choice", (), {"message": type("Message", (), {"content": '{"risks": []}'})()})()]},
            )()

    class FakeOpenAI:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

        def __init__(self, **kwargs):
            pass

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        openai_review,
        "retrieve_relevant_laws",
        lambda query_text: (_ for _ in ()).throw(RuntimeError("qdrant unavailable")),
    )

    response = review_contract_text("contract text", "contract.docx")

    assert len(response.risks) == len(openai_review.RULE_TOPICS)
    assert all(risk.source == "rule" for risk in response.risks)
    assert response.warnings
