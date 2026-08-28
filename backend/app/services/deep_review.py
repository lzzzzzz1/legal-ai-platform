"""Structured commercial deep review based on the in-house party playbook."""

from __future__ import annotations

import json
import os
from time import perf_counter

from fastapi import HTTPException, status
from openai import OpenAI

from app.schemas.review import DeepReviewOutput, DeepReviewSettings, ReviewCoverage, ReviewResponse
from app.services.openai_review import (
    BAILIAN_DEFAULT_BASE_URL,
    BAILIAN_DEFAULT_MODEL,
    _audit_review,
    _model_content_to_text,
    _parse_json_content,
    _normalize_risk_fields,
    _trim_contract_text,
    _validate_contract_anchors,
    bind_review_risks_to_unique_paragraphs,
    format_contract_with_paragraph_references,
    hydrate_review_clause_references,
    recover_unreferenced_risk_locations,
    repair_unlocatable_risk_quotes,
)
from app.services.openai_review import MAX_CONTRACT_CHARS
from app.services.document_preflight import PREFLIGHT_SCOPE, run_document_preflight


DEEP_REVIEW_REPAIR_MAX_CONTRACT_CHARS = int(os.getenv("DEEP_REVIEW_REPAIR_MAX_CONTRACT_CHARS", "16000"))
DEEP_REVIEW_REPAIR_MAX_OUTPUT_TOKENS = int(os.getenv("DEEP_REVIEW_REPAIR_MAX_OUTPUT_TOKENS", "2600"))


DEEP_REVIEW_PROMPT = """你是企业法务的深度合同审核助手。适用中国法律；你的结论仅供内部法务初审，重大、涉外、跨境数据、医疗健康数据、监管敏感或高争议金额事项必须标注人工复核。

你必须依据合同原文，不得编造事实、法律条文或附件内容。未约定或无法确认时明确写“合同未约定”或“待业务确认”。

按用户身份切换立场：甲方/采购方/客户/被许可方时，以甲方保护逻辑审查；乙方/供应商/服务方/许可方时，以乙方保护逻辑审查；其他角色按照说明保护相应一方。

按专业法务尽调的优先级使用“深度审查设置”：
1. party_role、non_negotiables 是用户已明确确认的立场和红线，优先级最高；
2. additional_notes 是用户用自然语言补充的业务想法、担忧或期望。将其作为审查偏好和待核对业务背景；若其与合同原文矛盾、没有合同依据或影响结论，须列入 clarification_questions，绝不能擅自写成合同已约定事实；
3. transaction_stage、timeline_urgency、counterparty_context 是用户确认的谈判情境，只用于确定审查排序、谈判策略和升级提示，绝不能被写成合同已约定的事实；
4. deal_priorities 是业务希望达成的交易结果，用于确定谈判目标和让步顺序；
5. focus_areas 和 special_requirements 是用户明确关切，但未列出的项目仍须做完整基础审查；
6. business_context 为空时，不得假设业务事实、预算、交付日期或数据类型。可按该角色的通用保护标准审查，并在 settings_note 中写明“系统默认审查假设”，将真正影响结论的未知事项列入 clarification_questions；
7. 合同概览、前端推荐或通用标准不是用户已确认事实，不得把它们写成合同事实或不可让步底线。

重点识别并按高风险优先级处理：单方变更、单方调价、预付款超过30%或付款未挂钩交付验收发票、验收标准缺失/默示验收/期限不足10个工作日、数据删除或AI训练/再识别/商业化/跨境、责任上限过低或数据/保密/IP/故意重大过失免责、无条件停服/限制账号、畸高退出成本、经营风险伪装不可抗力、对方所在地专属管辖、未经许可品牌宣传、合规与数据安全缺失。

每个风险必须可回链至精确合同原文或“【缺失该约定】”。高风险必须提供可直接写入合同的建议替换文本；同时给出谈判级别：must_modify、negotiable、internal_approval 或 prohibited，并在适当时给出最低可接受文本。

仅返回一个合法 JSON 对象，不要 Markdown。严格使用：
{
  "contract_type":"通用商务合同",
  "review_summary":"不超过500字的审查说明",
  "risks":[{
    "item":"检查项","level":"high|medium|low","original_text":"合同精确原文或【缺失该约定】","anchor_text":null,"insert_after_text":null,
    "clause_reference":"P001 格式的合同段落编号；缺失条款则填最相关的插入锚点段落编号","quote_start":0,"quote_end":0,"risk":"风险及对我方影响","party_impact":"对我方影响","suggestion":"可直接写入合同的完整条文","minimum_acceptable_text":"最低可接受条文或空字符串","negotiation_level":"must_modify|negotiable|internal_approval|prohibited","laws":[]
  }],
  "deep_review":{
    "state":"completed","overall_conclusion":"可签|有条件可签|不建议签|待确认","executive_summary":"不超过500字","key_facts":[{"item":"付款","contract_term":"合同约定或合同未约定","conclusion":"审查结论/待确认"}],
    "missing_clauses":["缺失条款及简要原因"],
    "negotiation_items":[{"topic":"事项","target":"我方目标","minimum_acceptable":"最低可接受条件","owner":"法务/业务/IT安全/财务/管理层"}],
    "clarification_questions":["真正影响结论的问题，最多10条"],
    "settings_note":"未提供的可选偏好已按通用商业标准处理"
  },
  "coverage":[]
}
合同正文会以 [P001] 形式标记段落编号。每个非缺失风险必须返回对应编号及 quote_start/quote_end（段落内从 0 开始、前闭后开）；系统将从原文切片生成最终引文，绝不能把 [P001] 编号写入 original_text。"""


def _build_deep_review(
    content: str,
    *,
    filename: str,
    contract_text: str,
    duration_ms: int,
) -> ReviewResponse:
    """Parse only the model-owned deep-review payload before post-processing.

    Keeping this boundary small lets us retry a malformed provider response
    without repeating safe, deterministic localisation and preflight work.
    """
    payload = _parse_json_content(content)
    if not isinstance(payload, dict):
        raise ValueError("Deep review response must be a JSON object.")
    payload["filename"] = filename
    payload["contract_text"] = contract_text
    payload["review_scope"] = ["深度商业与谈判审查"]
    payload["review_method"] = "model"
    payload["review_duration_ms"] = duration_ms
    review = ReviewResponse(**_normalize_risk_fields(payload))
    if review.deep_review is None or review.deep_review.state != "completed":
        raise ValueError("Deep review did not return a completed structured result.")
    if not review.review_summary.strip():
        raise ValueError("Deep review did not return a review summary.")
    return review


def _request_compact_deep_review(
    client: OpenAI,
    *,
    settings: DeepReviewSettings,
    indexed_contract: str,
) -> str:
    """Retry once with a compact schema when a gateway corrupts long JSON.

    Deliberately omit OpenAI ``response_format`` here. Some compatible
    gateways advertise JSON mode but tokenise long Chinese JSON into invalid
    fragments; the explicit prompt plus server-side validation is safer.
    """
    response = client.chat.completions.create(
        model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
        messages=[
            {
                "role": "system",
                "content": (
                    "你是企业法务助手。仅返回一个合法 JSON 对象，不要 Markdown、思考过程或任何 JSON 外文字。"
                    "每个风险必须引用合同原文，无法确认时使用【缺失该约定】。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "审查设置：" + json.dumps(settings.model_dump(), ensure_ascii=False)
                    + "\n返回此固定结构："
                    + '{"contract_type":"通用商务合同","review_summary":"简短审查说明",'
                    + '"risks":[{"item":"检查项","level":"high|medium|low",'
                    + '"original_text":"合同原文线索或【缺失该约定】","clause_reference":"P001或空字符串","quote_start":0,"quote_end":0,'
                    + '"risk":"风险说明","party_impact":"对我方影响","suggestion":"可写入合同的条款",'
                    + '"minimum_acceptable_text":"最低可接受条文或空字符串",'
                    + '"negotiation_level":"must_modify|negotiable|internal_approval|prohibited","laws":[]}],'
                    + '"deep_review":{"state":"completed","overall_conclusion":"可签|有条件可签|不建议签|待确认",'
                    + '"executive_summary":"简短结论","key_facts":[],"missing_clauses":[],"negotiation_items":[],'
                    + '"clarification_questions":[],"settings_note":""},"coverage":[]}'
                    + "\n合同正文：\n" + indexed_contract[:DEEP_REVIEW_REPAIR_MAX_CONTRACT_CHARS]
                ),
            },
        ],
        temperature=0,
        max_tokens=DEEP_REVIEW_REPAIR_MAX_OUTPUT_TOKENS,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = _model_content_to_text(response.choices[0].message.content)
    if not content:
        raise ValueError("Deep review compact retry returned an empty response.")
    return content


def review_contract_deeply(
    contract_text: str,
    filename: str,
    settings: DeepReviewSettings,
) -> ReviewResponse:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DASHSCOPE_API_KEY is not configured.")
    if len(contract_text) > MAX_CONTRACT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "The contract is too long for a single deep review request. "
                "Complete the segmented initial review, then shorten or split the contract before deep review."
            ),
        )

    started_at = perf_counter()
    content = ""
    indexed_contract, paragraph_references = format_contract_with_paragraph_references(contract_text)
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
            timeout=float(os.getenv("BAILIAN_DEEP_REVIEW_TIMEOUT_SECONDS", "180")),
        )
        response = client.chat.completions.create(
            model=os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": DEEP_REVIEW_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "深度审查设置：\n" + json.dumps(settings.model_dump(), ensure_ascii=False)
                        + "\n\n合同正文（段落编号仅用于定位，不属于合同内容）：\n" + _trim_contract_text(indexed_contract)
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=int(os.getenv("BAILIAN_DEEP_REVIEW_MAX_OUTPUT_TOKENS", "5000")),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            # Do not enable response_format here. The configured compatible
            # gateway corrupts large Chinese JSON while in forced JSON mode;
            # the prompt and strict backend schema remain the source of truth.
        )
        content = _model_content_to_text(response.choices[0].message.content)
        if not content:
            raise ValueError("Deep review model returned an empty response.")
    except HTTPException:
        raise
    except Exception as exc:
        _audit_review(
            filename=filename,
            raw_response="",
            duration_ms=round((perf_counter() - started_at) * 1000),
            status="deep_review_request_failed",
            risk_count=0,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Deep review model service is temporarily unavailable. Please retry after connectivity is restored.",
        ) from exc

    try:
        try:
            review = _build_deep_review(
                content,
                filename=filename,
                contract_text=contract_text,
                duration_ms=round((perf_counter() - started_at) * 1000),
            )
        except Exception as initial_error:
            # The model often has useful reasoning but a gateway can corrupt
            # its long response. Retry the original contract once using a much
            # smaller response shape rather than exposing a false failure.
            _audit_review(
                filename=filename,
                raw_response=content,
                duration_ms=round((perf_counter() - started_at) * 1000),
                status="deep_review_compact_retry_started",
                risk_count=0,
                error=str(initial_error),
            )
            compact_content = _request_compact_deep_review(
                client,
                settings=settings,
                indexed_contract=indexed_contract,
            )
            review = _build_deep_review(
                compact_content,
                filename=filename,
                contract_text=contract_text,
                duration_ms=round((perf_counter() - started_at) * 1000),
            )
            content = content + "\n[compact_retry]\n" + compact_content
            review.warnings = list(dict.fromkeys([
                *review.warnings,
                "模型首次返回格式异常，系统已使用简洁结构完成复核；建议继续人工核对全部修改。",
            ]))
        review = hydrate_review_clause_references(review, paragraph_references)
        review = bind_review_risks_to_unique_paragraphs(review, paragraph_references)
        review = recover_unreferenced_risk_locations(client, review, paragraph_references)
        review = repair_unlocatable_risk_quotes(client, review, contract_text)
        review = _validate_contract_anchors(review, contract_text)
        # The detailed pass is the single final analysis.  Add deterministic
        # draft-quality checks here rather than making users complete a
        # separate review stage before stating their commercial objective.
        review.preflight_checks = run_document_preflight(contract_text)
        review.review_scope = [PREFLIGHT_SCOPE, "深度商业与谈判审查"]
        # The model's coverage is a commercial-review signal.  Add an explicit
        # completed entry for the deterministic draft-quality pass so progress
        # never claims more completed scopes than it actually lists.
        review.coverage = [
            ReviewCoverage(
                topic=PREFLIGHT_SCOPE,
                status="checked",
                evidence="已执行合同框架、文字和标点的规则检查。",
                method="rule",
            ),
            ReviewCoverage(
                topic="深度商业与谈判审查",
                status="checked",
                evidence="已按用户确认的身份、关注点和业务诉求完成模型审查。",
                method="model",
            ),
            *[
                item
                for item in review.coverage
                if item.topic not in {PREFLIGHT_SCOPE, "深度商业与谈判审查"}
            ],
        ]
        missing_marker = "【缺失该约定】"
        unlocatable = [
            risk
            for risk in review.risks
            if (
                risk.original_text.strip() not in {missing_marker, "缺失该约定"}
                and risk.original_text.strip() not in contract_text
            )
            or (
                (risk.insert_after_text or risk.anchor_text or "").strip()
                and (risk.insert_after_text or risk.anchor_text or "").strip() not in contract_text
            )
        ]
        if unlocatable:
            review.warnings = list(dict.fromkeys([
                *review.warnings,
                f"深度审查中有 {len(unlocatable)} 项风险未能完整定位回合同原文，修改前必须人工核对。",
            ]))
        review.manual_review_required = True
        _audit_review(
            filename=filename,
            raw_response=content,
            duration_ms=review.review_duration_ms or 0,
            status="deep_review_completed",
            risk_count=len(review.risks),
        )
        return review
    except Exception as exc:
        _audit_review(
            filename=filename,
            raw_response=content,
            duration_ms=round((perf_counter() - started_at) * 1000),
            status="deep_review_invalid_response",
            risk_count=0,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="深度审查结果格式异常，系统未写入任何修改。已自动进行一次简洁复核仍未成功，请稍后重试；诊断信息已记录。",
        ) from exc
