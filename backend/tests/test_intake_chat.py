from app.schemas.review import ContractOverview, IntakeChatMessage, IntakeChatRequest, IntakeReviewCriteria
from app.services.intake_chat import _merge_criteria, continue_intake_chat


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
