"""Model-led conversation that turns business language into review criteria."""

from __future__ import annotations

import logging
import os
import json

from openai import OpenAI

from app.schemas.review import IntakeChatMessage, IntakeChatRequest, IntakeChatResponse, IntakeReviewCriteria
from app.services.contract_overview import _model_failure_reason
from app.services.openai_review import (
    BAILIAN_DEFAULT_BASE_URL,
    BAILIAN_DEFAULT_MODEL,
    _json_mode_options,
    _parse_json_content,
    _trim_contract_text,
)


logger = logging.getLogger(__name__)

_CRITERIA_LIST_LIMITS = {
    "deal_priorities": 6,
    "focus_areas": 8,
    "special_requirements": 8,
    "additional_notes": 5,
}
_CRITERIA_TEXT_LIMITS = {
    "other_party_role": 200,
    "business_context": 2_000,
    "non_negotiables": 2_000,
}

INTAKE_PROMPT = """你是企业法务的审查前沟通助手。任务是先帮助用户用自然语言确定审查标准，再交给后续合同审查流程。
你已获得合同概览和最近的对话。只能把用户明确表达的偏好、目标或底线写入 criteria；合同事实必须以概览或原文为准，不能编造。

遵循法务工作的第一性原则：依次弄清我方身份、交易要实现的业务结果、最担心的损失/失败情形、不可让步条件，以及希望的谈判力度。每轮只问一个最能减少不确定性的简短问题；不使用选择题或表单措辞，允许用户自由表达。

当已明确我方身份，且用户至少说明一个业务目标、担忧或底线时，可以 ready_for_review=true。此时 assistant_message 必须用简短文字复述将采用的审查标准，并问用户“如无补充，可点击开始综合审查”。如果信息尚不足，ready_for_review=false 并提出下一个问题。

只返回 JSON：
{
  "assistant_message":"自然语言回复（最多220字）",
  "criteria": {
    "party_role":"party_a|party_b|other|null",
    "other_party_role":"",
    "deal_priorities":["最多6项，来自用户表达"],
    "focus_areas":["最多8项，中文短语"],
    "review_style":"protective|balanced|material_only",
    "business_context":"用户业务目标和背景的简洁归纳",
    "non_negotiables":"用户明确的不可让步条件；没有则为空",
    "special_requirements":["最多8项"],
    "additional_notes":["最多5项"]
  },
  "ready_for_review":true|false
}
不要输出 Markdown、不要输出解释文本。"""


def _merge_criteria(current: IntakeReviewCriteria, candidate: object) -> IntakeReviewCriteria:
    if not isinstance(candidate, dict):
        return current
    payload = current.model_dump()
    for key in payload:
        value = candidate.get(key)
        if value is None:
            continue
        if key in _CRITERIA_LIST_LIMITS:
            if isinstance(value, list):
                payload[key] = [
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                ][:_CRITERIA_LIST_LIMITS[key]]
        elif key in _CRITERIA_TEXT_LIMITS and isinstance(value, str):
            payload[key] = value.strip()[:_CRITERIA_TEXT_LIMITS[key]]
    if candidate.get("party_role") in {"party_a", "party_b", "other"}:
        payload["party_role"] = candidate["party_role"]
    if candidate.get("review_style") in {"protective", "balanced", "material_only"}:
        payload["review_style"] = candidate["review_style"]
    return IntakeReviewCriteria(**payload)


def _fallback_turn(request: IntakeChatRequest, reason: str | None = None) -> IntakeChatResponse:
    criteria = request.criteria
    user_messages = [message.content.strip() for message in request.messages if message.role == "user" and message.content.strip()]
    last_user_message = user_messages[-1] if user_messages else ""
    combined = "\n".join(user_messages).lower()
    payload = criteria.model_copy(deep=True)
    if not payload.party_role:
        if any(token in combined for token in ("甲方", "采购方", "客户", "买方")):
            payload.party_role = "party_a"
        elif any(token in combined for token in ("乙方", "供应方", "服务方", "卖方")):
            payload.party_role = "party_b"
    if last_user_message:
        payload.business_context = "\n".join(filter(None, [payload.business_context, last_user_message]))[-2_000:]
        payload.additional_notes = [*payload.additional_notes, last_user_message][-5:]
    has_business_intent = bool(payload.business_context.strip() or payload.non_negotiables.strip() or payload.additional_notes)
    ready = bool(payload.party_role and has_business_intent)
    if not payload.party_role:
        message = "我已阅读合同概览。请先用一句话说明：您代表甲方/采购方、乙方/供应方，还是其他角色？"
    elif not has_business_intent:
        message = "了解。此次交易最希望实现什么结果，或最担心发生什么损失？请用日常语言说明即可。"
    elif ready:
        message = "我已记录您的立场与业务诉求。后续会把它们作为谈判偏好和审查标准，而不当作合同已约定事实。如无补充，可点击开始综合审查。"
    else:
        message = "还有没有绝对不能接受的条件，例如付款、验收、数据使用、责任或退出安排？"
    return IntakeChatResponse(
        assistant_message=message,
        criteria=payload,
        ready_for_review=ready,
        source="fallback",
        warning=f"模型对话暂不可用（{reason}），已使用本地问答引导。" if reason else None,
    )


def _request_model_turn(client: OpenAI, request: IntakeChatRequest) -> object:
    conversation = [
        {"role": message.role, "content": message.content}
        for message in request.messages[-12:]
    ]
    context = {
        "合同概览": request.overview.model_dump(),
        "当前审查标准": request.criteria.model_dump(),
        # Keep an intake turn quick while still letting the model verify the
        # overview against the opening provisions of the actual contract.
        "合同正文节选": _trim_contract_text(request.contract_text)[:12_000],
    }
    response = client.chat.completions.create(
        model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": INTAKE_PROMPT},
            {"role": "user", "content": "上下文：\n" + json.dumps(context, ensure_ascii=False)},
            *conversation,
        ],
        temperature=0.2,
        max_tokens=int(os.getenv("BAILIAN_INTAKE_MAX_OUTPUT_TOKENS", "900")),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **_json_mode_options(),
    )
    return _parse_json_content(response.choices[0].message.content or "")


def continue_intake_chat(request: IntakeChatRequest) -> IntakeChatResponse:
    """Use a stateless model turn; a deterministic fallback preserves the flow."""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return _fallback_turn(request, "未配置模型访问凭据")
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
            timeout=float(os.getenv("BAILIAN_INTAKE_TIMEOUT_SECONDS", "40")),
            max_retries=0,
        )
        payload = _request_model_turn(client, request)
        if not isinstance(payload, dict) or not isinstance(payload.get("assistant_message"), str):
            return _fallback_turn(request, "模型未返回可用的对话结构")
        criteria = _merge_criteria(request.criteria, payload.get("criteria"))
        ready = bool(payload.get("ready_for_review")) and bool(criteria.party_role) and bool(
            criteria.business_context.strip() or criteria.non_negotiables.strip() or criteria.additional_notes
        )
        return IntakeChatResponse(
            assistant_message=payload["assistant_message"].strip()[:2_000] or _fallback_turn(request).assistant_message,
            criteria=criteria,
            ready_for_review=ready,
            source="model",
        )
    except Exception as exc:
        logger.warning("Intake chat model call failed: %s", exc, exc_info=True)
        return _fallback_turn(request, _model_failure_reason(exc))
