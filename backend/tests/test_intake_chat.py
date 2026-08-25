from app.schemas.review import ContractOverview, IntakeChatMessage, IntakeChatRequest, IntakeReviewCriteria
from app.services.intake_chat import (
    _clean_quick_replies,
    _merge_criteria,
    _recover_assistant_message,
    continue_intake_chat,
)
from app.services.openai_review import _parse_json_content


def _request(messages: list[IntakeChatMessage], criteria: IntakeReviewCriteria | None = None) -> IntakeChatRequest:
    return IntakeChatRequest(
        contract_text="甲方采购乙方软件服务，验收后付款。",
        overview=ContractOverview(contract_type="软件服务合同", summary="甲方向乙方采购软件服务。"),
        messages=messages,
        criteria=criteria or IntakeReviewCriteria(),
    )


def test_intake_chat_fallback_first_asks_for_role(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    response = continue_intake_chat(_request([]))

    assert response.source == "fallback"
    assert response.ready_for_review is False
    assert "甲方" in response.assistant_message
    assert response.quick_replies == [
        "我代表甲方/采购方。",
        "我代表乙方/供应方。",
        "我是业务经办人，需要兼顾交易落地与风险控制。",
    ]
    assert len(response.suggested_questions) == 2


def test_intake_chat_fallback_builds_ready_criteria_from_free_text(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    messages = [
        IntakeChatMessage(role="assistant", content="请说明身份。"),
        IntakeChatMessage(
            role="user",
            content="我是甲方采购方，项目十月必须上线，不能接受默认验收或把客户数据用于 AI 训练。",
        ),
    ]

    response = continue_intake_chat(_request(messages))

    assert response.criteria.party_role == "party_a"
    assert response.ready_for_review is True
    assert "十月" in response.criteria.business_context
    assert response.quick_replies == []


def test_intake_chat_fallback_does_not_unlock_review_for_a_bare_role(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    response = continue_intake_chat(
        _request([IntakeChatMessage(role="user", content="我代表甲方/采购方。")])
    )

    assert response.criteria.party_role == "party_a"
    assert response.criteria.business_context == ""
    assert response.ready_for_review is False
    assert "最希望实现" in response.assistant_message


def test_quick_replies_are_deduplicated_clamped_and_length_limited() -> None:
    replies = _clean_quick_replies([
        "  坚持甲方所在地法院管辖。  ",
        "坚持甲方所在地法院管辖。",
        "接受被告所在地法院管辖。",
        "作为可谈判项处理。",
        "第四个有效选项。",
        "不会进入结果。",
    ])

    assert replies == [
        "坚持甲方所在地法院管辖。",
        "接受被告所在地法院管辖。",
        "作为可谈判项处理。",
        "第四个有效选项。",
    ]


def test_intake_chat_retries_once_when_model_json_is_invalid(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr("app.services.intake_chat.OpenAI", lambda **kwargs: object())
    attempts: list[bool] = []

    def fake_request(client, request, *, repair=False):
        attempts.append(repair)
        if not repair:
            raise ValueError("No valid JSON object or array was found in model output.")
        return {
            "assistant_message": "我先回答您的问题；如无补充，可以开始综合审查。",
            "quick_replies": [],
            "suggested_questions": ["这项条款还可以怎样修改？"],
            "criteria": {
                "party_role": "party_a",
                "business_context": "确保按期交付并完成验收",
            },
            "ready_for_review": True,
        }

    monkeypatch.setattr("app.services.intake_chat._request_model_turn", fake_request)
    criteria = IntakeReviewCriteria(
        party_role="party_a",
        business_context="确保按期交付并完成验收",
    )

    response = continue_intake_chat(_request([], criteria))

    assert attempts == [False, True]
    assert response.source == "model"
    assert response.ready_for_review is True
    assert response.suggested_questions == ["这项条款还可以怎样修改？"]


def test_intake_chat_retries_parseable_but_wrong_model_shape(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr("app.services.intake_chat.OpenAI", lambda **kwargs: object())
    attempts: list[bool] = []

    def fake_request(client, request, *, repair=False):
        attempts.append(repair)
        if not repair:
            return {"answer": "字段名错误"}
        return {
            "assistant_message": "已修复格式，可以继续确认审查方向。",
            "quick_replies": [],
            "suggested_questions": [],
            "criteria": {"party_role": "party_a", "business_context": "确保交付"},
            "ready_for_review": True,
        }

    monkeypatch.setattr("app.services.intake_chat._request_model_turn", fake_request)
    response = continue_intake_chat(_request([]))

    assert attempts == [False, True]
    assert response.source == "model"
    assert response.ready_for_review is True


def test_json_parser_tolerates_think_block_control_character_and_trailing_comma() -> None:
    payload = _parse_json_content(
        '<think>internal reasoning</think>{"assistant_message":"第一行\n第二行","criteria":{},}'
    )

    assert payload == {"assistant_message": "第一行\n第二行", "criteria": {}}


def test_recover_assistant_message_from_truncated_json() -> None:
    content = '{"assistant_message":"先回答用户的问题，再确认是否需要调整管辖条款。", "quick_replies": ['

    assert _recover_assistant_message(content) == "先回答用户的问题，再确认是否需要调整管辖条款。"


def test_intake_criteria_clamps_overlong_model_fields_without_losing_the_turn() -> None:
    criteria = _merge_criteria(
        IntakeReviewCriteria(),
        {
            "deal_priorities": [f"目标{i}" for i in range(10)],
            "focus_areas": [f"关注{i}" for i in range(10)],
            "special_requirements": [f"要求{i}" for i in range(10)],
            "additional_notes": [f"补充{i}" for i in range(10)],
            "other_party_role": "角色" * 150,
            "business_context": "背景" * 1_500,
            "non_negotiables": "底线" * 1_500,
        },
    )

    assert len(criteria.deal_priorities) == 6
    assert len(criteria.focus_areas) == 8
    assert len(criteria.special_requirements) == 8
    assert len(criteria.additional_notes) == 5
    assert len(criteria.other_party_role) == 200
    assert len(criteria.business_context) == 2_000
    assert len(criteria.non_negotiables) == 2_000


def test_follow_up_keeps_confirmed_review_direction_stable() -> None:
    current = IntakeReviewCriteria(
        party_role="party_a",
        focus_areas=["交付与验收"],
        review_style="balanced",
        business_context="确保按期上线",
    )

    criteria = _merge_criteria(
        current,
        {
            "party_role": "party_b",
            "focus_areas": ["知识产权"],
            "review_style": "protective",
            "business_context": "希望了解知识产权条款",
        },
    )

    assert criteria.party_role == "party_a"
    assert criteria.review_style == "balanced"
    assert criteria.focus_areas == ["交付与验收", "知识产权"]
    assert "确保按期上线" in criteria.business_context
    assert "希望了解知识产权条款" in criteria.business_context
