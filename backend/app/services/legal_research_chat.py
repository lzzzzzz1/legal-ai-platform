"""Cautious, general legal-information chat used alongside contract intake."""

from __future__ import annotations

import logging
import os

from openai import OpenAI

from app.schemas.review import LegalResearchRequest, LegalResearchResponse
from app.services.contract_overview import _model_failure_reason
from app.services.openai_review import BAILIAN_DEFAULT_BASE_URL, BAILIAN_DEFAULT_MODEL, _model_content_to_text


logger = logging.getLogger(__name__)

LEGAL_RESEARCH_PROMPT = """你是企业法务助手，回答中国法语境下的通用法规、合同条款与合规问题。

回答原则：
1. 先直接回答用户问题，再说明其对合同谈判或履行的实际影响；不要把法规查询改写成审查方向，也不要要求用户上传合同。
2. 只有在能确认时才引用法律、法规、司法解释或条款编号；不能确认现行版本、适用地区或具体条文时，明确说明需要以国家法律法规数据库、主管机关或律师核验为准，绝不编造法条。
3. 这不是正式法律意见，不对具体争议结果作保证。涉及诉讼时效、监管许可、劳动、税务、数据出境、反垄断、证券等高风险场景时，应提示核验最新规则和专业人士意见。
4. 若提供了“合同上下文”，它仅用于解释用户问题与该合同条款的关系；合同事实不足时必须说“合同中未见明确约定”或“需查看对应条款”，不能补充虚构事实。
5. 使用清晰的中文，结构可包括“结论 / 依据与适用 / 合同提示”，通常不超过 700 字。
6. 末尾可以提出 2-4 个用户下一步可能会问的具体问题，但不要生成空泛选项或捏造合同事实。
"""


def _clean_text(value: str) -> str:
    return value.replace("```", "").replace("**", "").strip()[:2_500]


def _fallback(request: LegalResearchRequest, reason: str | None = None) -> LegalResearchResponse:
    latest = next((item.content.strip() for item in reversed(request.messages) if item.role == "user" and item.content.strip()), "该法规问题")
    warning = "法规问答模型暂不可用，以下内容不构成对具体法律条文的核验。"
    if reason:
        warning = f"法规问答模型暂不可用（{reason}），以下内容不构成对具体法律条文的核验。"
    return LegalResearchResponse(
        assistant_message=(
            f"我已记录您的问题：“{latest[:180]}”。目前无法可靠查询并核验最新法规条文，"
            "请提供法规名称、拟适用地区/场景及希望确认的具体条款；涉及签约、监管或争议决策时，"
            "请以国家法律法规数据库、主管机关公布文本或专业律师意见为准。"
        ),
        suggested_questions=[
            "这项法规在合同里通常需要落到哪些具体条款？",
            "请说明该问题需要核验的法律名称、版本和适用条件。",
        ],
        source="fallback",
        warning=warning,
    )


def _request_model_answer(client: OpenAI, request: LegalResearchRequest) -> LegalResearchResponse:
    conversation = [{"role": message.role, "content": message.content} for message in request.messages[-12:]]
    context = request.contract_context.strip()
    context_message = (
        "以下为用户上传合同的节选，仅在问题涉及该合同条款时参考：\n" + context
        if context
        else "当前没有上传合同；请将问题作为独立法规咨询回答。"
    )
    response = client.chat.completions.create(
        model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": LEGAL_RESEARCH_PROMPT},
            {"role": "user", "content": context_message},
            *conversation,
        ],
        temperature=0.2,
        max_tokens=int(os.getenv("BAILIAN_LEGAL_RESEARCH_MAX_OUTPUT_TOKENS", "1800")),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = _clean_text(_model_content_to_text(response.choices[0].message.content))
    if not content:
        raise ValueError("Legal research model returned an empty response.")
    return LegalResearchResponse(
        assistant_message=content,
        suggested_questions=[
            "这项规定在合同中通常应如何写成可执行条款？",
            "适用这项规定还需要确认哪些事实或例外？",
        ],
        source="model",
    )


def continue_legal_research_chat(request: LegalResearchRequest) -> LegalResearchResponse:
    """Answer general legal questions without altering the active review criteria."""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return _fallback(request, "未配置模型访问凭据")
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
            timeout=float(os.getenv("BAILIAN_LEGAL_RESEARCH_TIMEOUT_SECONDS", "45")),
            max_retries=0,
        )
        return _request_model_answer(client, request)
    except Exception as exc:
        logger.warning("Legal research model call failed: %s", exc, exc_info=True)
        return _fallback(request, _model_failure_reason(exc))
