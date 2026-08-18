"""Fast orientation pass used before a user supplies review instructions."""

from __future__ import annotations

import os
import logging

from openai import OpenAI

from app.schemas.review import (
    ContractOverview,
    ContractOverviewDecisionPoint,
    ContractOverviewDimension,
)
from app.services.openai_review import (
    BAILIAN_DEFAULT_BASE_URL,
    BAILIAN_DEFAULT_MODEL,
    _json_mode_options,
    _parse_json_content,
    _trim_contract_text,
)


logger = logging.getLogger(__name__)

OVERVIEW_PROMPT = """你是合同阅读助手，只做中性概览，不做风险判断、不建议修改。
用户需要先充分理解合同原文，再说明自己的业务诉求。因此 summary 必须是一份详细、易读的“合同内容概述”，而不是合同开头的摘抄。
阅读合同后仅返回 JSON：
{
  "contract_type":"合同类型",
  "summary":"350-700字的分段概述。按‘交易目的与标的、合同主体与角色、双方主要义务、金额与付款、交付/验收/服务、期限及其他重要约定’顺序提炼；合同未明确的事项可写‘合同未见明确约定’，不得编造，也不得作风险判断或修改建议。各段用换行分隔。",
  "parties":["合同中出现的主体及角色"],
  "transaction_subject":"标的或服务",
  "key_terms":["金额、期限、交付等已明确事项，最多8项"]
}
summary 与 key_terms 的每个事实都必须能在合同原文中找到依据。不得编造合同未出现的事实。"""

OVERVIEW_REPAIR_PROMPT = """你刚才的输出不是可被系统读取的合同概览。
请重新阅读下面的合同，严格只返回一个 JSON 对象（最外层必须是 {，不能是数组、不能有 Markdown、不能附加解释）。
对象必须包含 contract_type、summary、parties、transaction_subject、key_terms。
summary 必须是350-700字、分段且完整的合同内容概述，不能摘抄或截断合同开头。仅提取合同中已有事实，不做风险判断或修改建议。"""


_FALLBACK_DIMENSIONS = (
    ("交易结构", ("采购", "服务", "系统", "产品", "项目", "标的")),
    ("金额与付款", ("金额", "价款", "付款", "发票", "税", "费用", "元")),
    ("期限与生效", ("期限", "生效", "起算", "续约", "有效期")),
    ("交付与验收", ("交付", "验收", "上线", "里程碑", "交货")),
    ("质量与售后", ("质保", "保修", "维护", "售后", "支持")),
    ("数据、保密与知识产权", ("数据", "保密", "知识产权", "著作权", "源代码", "许可")),
    ("责任、退出与争议", ("违约", "赔偿", "解除", "终止", "争议", "仲裁", "法院")),
)


def _fallback_dimensions(contract_text: str) -> list[ContractOverviewDimension]:
    paragraphs = [" ".join(part.split()) for part in contract_text.splitlines() if part.strip()]
    dimensions: list[ContractOverviewDimension] = []
    for category, keywords in _FALLBACK_DIMENSIONS:
        evidence = next((paragraph for paragraph in paragraphs if any(keyword.lower() in paragraph.lower() for keyword in keywords)), "")
        dimensions.append(
            ContractOverviewDimension(
                category=category,
                status="partial" if evidence else "not_found",
                details=[evidence[:180]] if evidence else ["合同文本中未能识别明确约定。"],
            )
        )
    return dimensions


def _fallback_overview(contract_text: str, reason: str | None = None) -> ContractOverview:
    """Return a useful, source-grounded overview when the model is unavailable.

    The fallback must itself be dependency-free: it is the last line of defence
    for the upload-to-intake flow, so an error in this branch must never turn a
    recoverable model problem into an HTTP 500 response.
    """
    opening = " ".join(contract_text.split())[:180]
    parties = []
    for label in ("甲方", "乙方", "Party A", "Party B"):
        if label.lower() in contract_text.lower():
            parties.append(label)
    return ContractOverview(
        # Do not present a cut-off raw paragraph as if it were a model-written
        # summary.  The structured local facts below remain usable, while the
        # warning clearly explains why a richer narrative is unavailable.
        summary="已读取合同文本，但模型概览暂不可用。请结合合同正文确认交易目的、双方职责、金额、交付与验收等内容后，在右侧补充您的业务诉求。",
        parties=parties,
        transaction_subject="待根据业务背景确认",
        key_terms=[f"原文开头提及：{opening}…"] if opening else [],
        dimensions=_fallback_dimensions(contract_text),
        business_flow=["已读取合同文本；模型概览暂不可用，暂不能可靠生成履约流程说明。"],
        decision_points=[
            ContractOverviewDecisionPoint(
                topic="我方审查立场",
                contract_position="合同文本无法替代业务方确认我方角色与交易目标。",
                user_question="请确认我方是甲方、乙方还是其他角色，并说明最希望争取的结果。",
            )
        ],
        clarification_questions=["我方在本合同中属于甲方、乙方还是其他角色？"],
        method="fallback",
        warnings=[
            "模型概览未完成（%s），已切换为本地文本提取；请以原合同为准。" % (reason or "服务暂不可用")
        ],
    )


def _model_failure_reason(exc: Exception) -> str:
    """Map provider failures to safe, actionable UI text without leaking secrets."""
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if "authentication" in name or "authentication" in message or "401" in message or "403" in message:
        return "模型认证失败"
    if "timeout" in name or "timeout" in message:
        return "模型响应超时"
    if "connection" in name or "connect" in message or "network" in message:
        return "模型连接异常"
    if "validation" in name or "json" in message or "parse" in message:
        return "模型返回的概览结构不完整"
    return "模型服务暂不可用"


def _request_overview_payload(client: OpenAI, contract_text: str, prompt: str) -> object:
    """Call the compatible endpoint once and parse its JSON-shaped response."""
    response = client.chat.completions.create(
        model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "合同正文：\n" + _trim_contract_text(contract_text)},
        ],
        temperature=0,
        max_tokens=int(os.getenv("BAILIAN_OVERVIEW_MAX_OUTPUT_TOKENS", "1400")),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **_json_mode_options(),
    )
    return _parse_json_content(response.choices[0].message.content or "")


def create_contract_overview(contract_text: str) -> ContractOverview:
    """Use the model when available; a neutral local fallback keeps intake usable."""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return _fallback_overview(contract_text, "未配置模型访问凭据")
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
            timeout=float(os.getenv("BAILIAN_OVERVIEW_TIMEOUT_SECONDS", "45")),
            max_retries=int(os.getenv("BAILIAN_OVERVIEW_MAX_RETRIES", "1")),
        )
        payload = _request_overview_payload(client, contract_text, OVERVIEW_PROMPT)
        if not isinstance(payload, dict):
            # Some OpenAI-compatible gateways occasionally return a top-level
            # list despite the requested object. Retry once with a short,
            # explicit compatibility instruction before falling back locally.
            logger.warning("Contract overview model returned a non-object JSON payload; retrying once.")
            payload = _request_overview_payload(client, contract_text, OVERVIEW_REPAIR_PROMPT)
        if not isinstance(payload, dict):
            logger.warning("Contract overview model retry returned a non-object JSON payload.")
            return _fallback_overview(contract_text, "模型未返回可用的概览数据")
        payload["method"] = "model"
        payload["warnings"] = []
        try:
            return ContractOverview(**payload)
        except Exception as exc:
            logger.warning("Contract overview model payload failed schema validation: %s", exc)
            return _fallback_overview(contract_text, _model_failure_reason(exc))
    except Exception as exc:
        # Preserve the technical traceback in the local backend log for
        # operations, while the browser gets only a safe and actionable label.
        logger.warning("Contract overview model call failed: %s", exc, exc_info=True)
        return _fallback_overview(contract_text, _model_failure_reason(exc))
