import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services import openai_review
from app.services.openai_review import parse_review_response, review_contract_text


def test_parse_review_response_accepts_risks_object() -> None:
    response = parse_review_response(
        content=(
            '{"risks":[{"item":"税务条款","level":"high",'
            '"original_text":"税费承担未约定。",'
            '"risk":"缺少税费承担约定","suggestion":"补充税费承担主体。",'
            '"laws":["《中华人民共和国民法典》第四百七十条"]}]}'
        ),
        filename="contract.docx",
    )

    assert response.filename == "contract.docx"
    assert response.risks[0].item == "税务条款"
    assert response.risks[0].original_text == "税费承担未约定。"
    assert response.risks[0].laws == ["《中华人民共和国民法典》第四百七十条"]


def test_parse_review_response_accepts_top_level_array() -> None:
    response = parse_review_response(
        content=(
            '[{"item":"合同份数","level":"low",'
            '"original_text":"合同份数未约定。",'
            '"risk":"份数约定不清","suggestion":"明确一式几份。"}]'
        ),
        filename="contract.docx",
    )

    assert response.risks[0].level == "low"
    assert response.risks[0].laws == []


def test_parse_review_response_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        parse_review_response(
            content=(
                '{"risks":[{"item":"联系人信息","level":"critical",'
                '"original_text":"联系人未约定。",'
                '"risk":"缺少联系人","suggestion":"补充联系人。"}]}'
            ),
            filename="contract.docx",
        )


def test_review_contract_text_requires_dashscope_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        review_contract_text(contract_text="合同文本", filename="contract.docx")

    assert exc_info.value.status_code == 503
    assert "DASHSCOPE_API_KEY" in exc_info.value.detail


def test_review_contract_text_injects_retrieved_laws(monkeypatch) -> None:
    captured_messages = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured_messages["messages"] = kwargs["messages"]
            return type(
                "FakeResponse",
                (),
                {
                    "choices": [
                        type(
                            "FakeChoice",
                            (),
                            {
                                "message": type(
                                    "FakeMessage",
                                    (),
                                    {
                                        "content": (
                                            '{"risks":[{"item":"签订地点","level":"medium",'
                                            '"original_text":"合同约定履行地点不明确。",'
                                            '"risk":"履行地点约定不明确",'
                                            '"suggestion":"根据《中华人民共和国民法典》第五百一十一条补充履行地点。",'
                                            '"laws":["《中华人民共和国民法典》第五百一十一条"]}]}'
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        chat = FakeChat()

        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        openai_review,
        "retrieve_relevant_laws",
        lambda query_text: [
            {
                "label": "《中华人民共和国民法典》第五百一十一条",
                "content": "履行地点不明确的规则。",
            }
        ],
    )

    response = review_contract_text(contract_text="合同约定履行地点不明确。", filename="contract.docx")

    user_prompt = captured_messages["messages"][1]["content"]
    assert "参考法条" in user_prompt
    assert "original_text" in user_prompt
    assert "《中华人民共和国民法典》第五百一十一条" in user_prompt
    assert response.risks[0].laws == ["《中华人民共和国民法典》第五百一十一条"]
