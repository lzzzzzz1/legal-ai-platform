"""Model-led conversation that turns business language into review criteria."""

from __future__ import annotations

import logging
import os
import json
import re

from openai import OpenAI

from app.schemas.review import IntakeChatMessage, IntakeChatRequest, IntakeChatResponse, IntakeReviewCriteria
from app.services.contract_overview import _model_failure_reason
from app.services.openai_review import (
    BAILIAN_DEFAULT_BASE_URL,
    BAILIAN_DEFAULT_MODEL,
    _json_mode_options,
    _model_content_to_text,
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


class _IntakeModelOutputTruncated(ValueError):
    """A response stopped at the provider token limit and cannot be repaired safely."""

INTAKE_PROMPT = """你是企业法务的审查前沟通助手。任务是先帮助用户用自然语言确定审查标准，再交给后续合同审查流程。
你已获得合同概览和最近的对话。只能把用户明确表达的偏好、目标或底线写入 criteria；合同事实必须以概览或原文为准，不能编造。

每轮必须按以下顺序处理：
1. 先理解用户最新一句话。如果用户提出问题、要求解释、要求示例或要求展示修改方案，必须先直接回答；不得跳过用户问题而机械重复上一轮追问。
2. 回答必须以合同概览、正文节选和已确认信息为依据。缺少依据时明确说明未知，不得虚构合同事实、法律结论或用户偏好。
3. 可以像专业对话助手一样解释条款含义、分析利弊、比较方案并给出示例修改，但必须区分“合同已有内容”“基于用户立场的建议”和“仍待用户确认的事实”。
4. 再判断是否仍有一个会实质影响审查方向的未知信息。确有必要时，只在回答后追问一个简短问题；不必要时不追问，不要为了推进流程强行提问。
5. 当前审查标准是已经确认的稳定基线。普通咨询和追问不得删除、重置或悄悄改变已有身份、目标、重点、底线和审查风格；只有用户明确表示修改、取消或纠正时才能改写。
6. 不得逐字重复最近已经问过的问题。若用户未回答旧问题，应先回应其新问题，再用更具体、更容易回答的方式确认旧问题。

遵循法务工作的第一性原则：逐步弄清我方身份、交易要实现的业务结果、最担心的损失/失败情形、不可让步条件，以及希望的谈判力度。允许用户自由表达，不把对话做成固定表单。

当已明确我方身份，且用户至少说明一个业务目标、担忧或底线时，可以 ready_for_review=true。首次形成方案或用户明确调整方案时，简短复述审查标准并提示可以开始综合审查；此后的普通咨询应直接回答，不必每轮重复整套方案。信息尚不足时 ready_for_review=false。

quick_replies 规则：
- 仅针对 assistant_message 末尾实际提出的那个问题生成 2-4 个快捷回答；没有追问时返回空数组；
- 每项都是用户可以直接发送的一句话，并且必须直接回答本轮问题；选项之间应代表真实、常见且互相区分的审核立场；
- 只能表达可选择的身份、目标、风险偏好、谈判底线或确认结果，不得替用户捏造金额、日期、承诺、合同事实或专业结论；
- 不要生成“其他”“自行补充”这类没有实际回答内容的空选项，用户仍可在输入框自由作答。

suggested_questions 规则：
- 根据本轮回答、合同内容和当前审核方向，预测用户接下来最可能继续询问的 2-4 个有价值问题；
- 每项必须写成用户可以直接发送的问题，例如“这项条款对甲方最不利的地方是什么？”；点击后模型应能继续分析；
- 建议问题不能预设合同中不存在的金额、日期、主体承诺或风险结论，也不能诱导改变已确认的整体审核方向；
- suggested_questions 是“用户可能继续问什么”，quick_replies 是“用户如何回答 AI 当前追问”，二者不能混淆或重复。

只返回 JSON：
{
  "assistant_message":"先回应用户，可进行解释和分析，再按需追问（一般不超过600字）",
  "quick_replies":["直接回答本轮问题的选项，最多4项；无追问则为空"],
  "suggested_questions":["用户接下来可能询问的问题，2-4项"],
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

INTAKE_REPAIR_PROMPT = INTAKE_PROMPT + """

这是一次格式修复重试。上一次响应无法解析。请压缩 criteria 中的文字，确保整个响应是一个完整、可由 json.loads 直接解析的 JSON 对象。不要输出代码围栏、前后说明或任何 JSON 之外的字符。"""


def _merge_criteria(
    current: IntakeReviewCriteria,
    candidate: object,
    *,
    allow_rewrite: bool = False,
    allow_style_update: bool = False,
) -> IntakeReviewCriteria:
    if not isinstance(candidate, dict):
        return current
    payload = current.model_dump()
    for key in payload:
        value = candidate.get(key)
        if value is None:
            continue
        if key in _CRITERIA_LIST_LIMITS:
            if isinstance(value, list):
                incoming = [
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                ]
                existing = [] if allow_rewrite else payload[key]
                payload[key] = list(dict.fromkeys([*existing, *incoming]))[:_CRITERIA_LIST_LIMITS[key]]
        elif key in _CRITERIA_TEXT_LIMITS and isinstance(value, str):
            incoming = value.strip()
            existing = str(payload[key]).strip()
            if not incoming:
                if allow_rewrite:
                    payload[key] = ""
            elif allow_rewrite or not existing:
                payload[key] = incoming[:_CRITERIA_TEXT_LIMITS[key]]
            elif existing in incoming:
                payload[key] = incoming[:_CRITERIA_TEXT_LIMITS[key]]
            elif incoming not in existing:
                payload[key] = f"{existing}；{incoming}"[:_CRITERIA_TEXT_LIMITS[key]]
    if candidate.get("party_role") in {"party_a", "party_b", "other"} and (allow_rewrite or not current.party_role):
        payload["party_role"] = candidate["party_role"]
    if candidate.get("review_style") in {"protective", "balanced", "material_only"} and (allow_rewrite or allow_style_update):
        payload["review_style"] = candidate["review_style"]
    return IntakeReviewCriteria(**payload)


def _clean_quick_replies(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        answer = " ".join(item.split()).strip()[:120]
        if answer and answer not in cleaned:
            cleaned.append(answer)
        if len(cleaned) == 4:
            break
    return cleaned


def _clean_assistant_message(value: str) -> str:
    """Keep the chat readable in the plain-text UI when the model adds Markdown emphasis."""
    return value.replace("```", "").replace("**", "").strip()[:2_000]


def _payload_is_usable(payload: object) -> bool:
    """Reject parseable-but-wrong model shapes so they receive a repair turn."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("assistant_message"), str)
        and bool(payload["assistant_message"].strip())
        and (payload.get("criteria") is None or isinstance(payload.get("criteria"), dict))
    )


def _recover_assistant_message(content: str) -> str:
    """Recover a useful answer when a long JSON object is truncated after its message."""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    match = re.search(r'"assistant_message"\s*:\s*"', cleaned)
    if match:
        start = match.end()
        escaped = False
        chars: list[str] = []
        for char in cleaned[start:]:
            if escaped:
                chars.append({"n": "\n", "r": "\n", "t": " ", '"': '"', "\\": "\\"}.get(char, char))
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            else:
                chars.append(char)
        message = _clean_assistant_message("".join(chars))
        if message:
            return message
    # A provider may ignore the JSON instruction and return a plain answer.
    plain = cleaned.replace("```json", "").replace("```", "").strip()
    if plain and not plain.startswith(("{", "[")):
        return _clean_assistant_message(plain)
    return ""


def _requests_direction_change(request: IntakeChatRequest) -> bool:
    user_messages = [message.content for message in request.messages if message.role == "user"]
    if not user_messages:
        return False
    latest = user_messages[-1]
    return any(token in latest for token in ("改为", "更改", "调整为", "取消", "删除", "不再", "不是", "纠正", "重新设定", "换成"))


def _user_sets_review_style(request: IntakeChatRequest) -> bool:
    user_messages = [message.content for message in request.messages if message.role == "user"]
    if not user_messages:
        return False
    latest = user_messages[-1]
    return any(token in latest for token in ("严格保护", "争取我方", "兼顾合作", "平衡风险", "只提示重大", "仅提示重大"))


def _contains_substantive_business_intent(message: str) -> bool:
    """Return whether a turn adds more than a bare party-role declaration.

    The local fallback must not treat “我是甲方” as both the user's identity
    and their business goal.  Doing so used to unlock review one turn too
    early whenever the model service was unavailable.  Keep this deliberately
    conservative: users can still express a goal in any natural language, but
    common role-only phrases never satisfy the second intake requirement.
    """
    normalized = re.sub(r"[\s，。；：、,.!！?？/（）()\-—]", "", message.lower())
    normalized = re.sub(
        r"(?:我是|我代表|本方是|我方是|作为)?(?:甲方|乙方|采购方|供应方|服务方|客户|买方|卖方|被许可方|许可方|业务经办人)",
        "",
        normalized,
    )
    normalized = re.sub(r"(?:和|及|或|方|角色|一方)+", "", normalized)
    return len(normalized) >= 4


def _is_question(message: str) -> bool:
    return any(signal in message for signal in (
        "？", "?", "什么", "如何", "为什么", "是否", "能否", "哪", "哪些", "多少", "请分析", "请解释", "请比较",
    ))


def _recover_unstructured_model_turn(content: str, request: IntakeChatRequest) -> dict[str, object] | None:
    """Keep a readable contract answer when a compatible model ignores JSON."""
    message = _recover_assistant_message(content)
    if not message:
        return None
    fallback = _fallback_turn(request)
    return {
        "assistant_message": message,
        "quick_replies": fallback.quick_replies,
        "suggested_questions": fallback.suggested_questions,
        "criteria": fallback.criteria.model_dump(),
        "ready_for_review": fallback.ready_for_review,
    }


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
    # Contract questions must be answered, not silently saved as a new review
    # goal. The user's confirmed audit direction stays stable while discussing
    # a particular clause.
    if last_user_message and not _is_question(last_user_message) and _contains_substantive_business_intent(last_user_message):
        payload.business_context = "\n".join(filter(None, [payload.business_context, last_user_message]))[-2_000:]
        payload.additional_notes = [*payload.additional_notes, last_user_message][-5:]
    has_business_intent = bool(payload.business_context.strip() or payload.non_negotiables.strip() or payload.additional_notes)
    ready = bool(payload.party_role and has_business_intent)
    if not payload.party_role:
        message = "我已阅读合同概览。请先用一句话说明：您代表甲方/采购方、乙方/供应方，还是其他角色？"
        quick_replies = ["我代表甲方/采购方。", "我代表乙方/供应方。", "我是业务经办人，需要兼顾交易落地与风险控制。"]
        suggested_questions = ["这份合同的主要交易内容是什么？", "合同中有哪些关键金额和履行期限？"]
    elif not has_business_intent:
        message = "了解。此次交易最希望实现什么结果，或最担心发生什么损失？请用日常语言说明即可。"
        quick_replies = ["确保按期交付并通过明确验收。", "控制总成本，并让付款与履约结果挂钩。", "保护数据、保密信息和交付成果权利。", "降低延期、违约和退出造成的损失。"]
        suggested_questions = ["这份合同目前怎样约定交付和验收？", "付款条件与履约结果是否已经挂钩？"]
    elif ready:
        message = "我已记录您的立场与业务诉求。后续会把它们作为谈判偏好和审查标准，而不当作合同已约定事实。如无补充，可点击开始综合审查。"
        quick_replies = ["按当前方案开始审查", "严格保护我方利益", "平衡合作与风险", "只提示重大问题"]
        suggested_questions = ["这份合同最值得优先修改的三处是什么？", "请展示关键风险条款的具体修改方案。", "哪些约定可能导致我方承担额外损失？"]
    else:
        message = "还有没有绝对不能接受的条件，例如付款、验收、数据使用、责任或退出安排？"
        quick_replies = ["不接受默认验收或视为验收通过。", "不接受未经书面同意使用或转移我方数据。", "不接受责任明显不对等或免责范围过宽。", "目前没有明确的不可让步条件。"]
        suggested_questions = ["当前合同里是否存在默认验收？", "责任限制和免责条款对我方是否公平？"]
    return IntakeChatResponse(
        assistant_message=message,
        quick_replies=quick_replies,
        suggested_questions=suggested_questions,
        criteria=payload,
        ready_for_review=ready,
        source="fallback",
        warning=f"模型对话暂不可用（{reason}），已使用本地问答引导。" if reason else None,
    )


def _request_model_turn(client: OpenAI, request: IntakeChatRequest, *, repair: bool = False) -> object:
    conversation = [
        {"role": message.role, "content": message.content}
        for message in request.messages[-12:]
    ]
    context = {
        "合同概览": request.overview.model_dump(),
        "当前审查标准": request.criteria.model_dump(),
        # Intake is about confirming the user's direction, not re-reviewing
        # the entire document. A bounded excerpt keeps ordinary turns fast
        # while the structured overview retains the key contract facts.
        "合同正文节选": _trim_contract_text(request.contract_text)[:int(os.getenv("BAILIAN_INTAKE_CONTEXT_MAX_CHARS", "6000"))],
    }
    response = client.chat.completions.create(
        model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": INTAKE_REPAIR_PROMPT if repair else INTAKE_PROMPT},
            {"role": "user", "content": "上下文：\n" + json.dumps(context, ensure_ascii=False)},
            *conversation,
        ],
        temperature=0.2,
        max_tokens=int(os.getenv("BAILIAN_INTAKE_MAX_OUTPUT_TOKENS", "1600")),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **_json_mode_options(),
    )
    content = _model_content_to_text(response.choices[0].message.content)
    try:
        return _parse_json_content(content)
    except ValueError:
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        logger.warning(
            "Intake model JSON parse failed (repair=%s, finish_reason=%s, chars=%s)",
            repair,
            finish_reason,
            len(content),
        )
        recovered_turn = _recover_unstructured_model_turn(content, request)
        if recovered_turn:
            return recovered_turn
        if finish_reason == "length":
            # A second call with the same large conversation tends to time out
            # again. Keep the user's confirmed direction and fall back
            # immediately instead of making the UI wait through two failures.
            raise _IntakeModelOutputTruncated("Intake model response was truncated.")
        if repair:
            return None
        raise


def _intake_failure_reason(exc: Exception) -> str:
    """Use intake-specific wording instead of mislabeling chat JSON as an overview error."""
    reason = _model_failure_reason(exc)
    if reason == "模型返回的概览结构不完整":
        return "模型返回格式异常"
    return reason


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
        try:
            payload = _request_model_turn(client, request)
            if not _payload_is_usable(payload):
                raise ValueError("Model returned a parseable but unusable intake payload.")
        except _IntakeModelOutputTruncated as exc:
            logger.warning("Intake chat model response was truncated; using local guidance: %s", exc)
            return _fallback_turn(request, "模型回复过长，已使用本地问答引导")
        except ValueError as exc:
            logger.warning("Intake chat model returned invalid output; retrying once: %s", exc)
            payload = _request_model_turn(client, request, repair=True)
            if not _payload_is_usable(payload):
                raise ValueError("Repair turn returned an unusable intake payload.")
        criteria = _merge_criteria(
            request.criteria,
            payload.get("criteria"),
            allow_rewrite=_requests_direction_change(request),
            allow_style_update=_user_sets_review_style(request),
        )
        criteria_complete = bool(criteria.party_role) and bool(
            criteria.business_context.strip() or criteria.non_negotiables.strip() or criteria.additional_notes
        )
        # Once the necessary role and one business aim, concern or bottom line
        # are known, the user must always be able to begin review. Optional
        # follow-up questions may enrich the plan but cannot trap the workflow.
        ready = criteria_complete
        quick_replies = _clean_quick_replies(payload.get("quick_replies"))
        if not quick_replies and not criteria_complete:
            quick_replies = _fallback_turn(request.model_copy(update={"criteria": criteria})).quick_replies
        suggested_questions = [
            question
            for question in _clean_quick_replies(payload.get("suggested_questions"))
            if question not in quick_replies
        ][:4]
        return IntakeChatResponse(
            assistant_message=_clean_assistant_message(payload["assistant_message"]) or _fallback_turn(request).assistant_message,
            quick_replies=quick_replies,
            suggested_questions=suggested_questions,
            criteria=criteria,
            ready_for_review=ready,
            source="model",
        )
    except Exception as exc:
        logger.warning("Intake chat model call failed: %s", exc, exc_info=True)
        return _fallback_turn(request, _intake_failure_reason(exc))
