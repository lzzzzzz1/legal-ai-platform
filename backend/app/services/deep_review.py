"""Structured commercial deep review based on the in-house party playbook."""

from __future__ import annotations

import json
import os
from time import perf_counter

from fastapi import HTTPException, status
from openai import OpenAI

from app.schemas.review import DeepReviewOutput, DeepReviewSettings, ReviewResponse
from app.services.openai_review import (
    BAILIAN_DEFAULT_BASE_URL,
    BAILIAN_DEFAULT_MODEL,
    _audit_review,
    _json_mode_options,
    _parse_json_content,
    _normalize_risk_fields,
    _trim_contract_text,
    _validate_contract_anchors,
)
from app.services.openai_review import MAX_CONTRACT_CHARS


DEEP_REVIEW_PROMPT = """你是企业法务的深度合同审核助手。适用中国法律；你的结论仅供内部法务初审，重大、涉外、跨境数据、医疗健康数据、监管敏感或高争议金额事项必须标注人工复核。

你必须依据合同原文，不得编造事实、法律条文或附件内容。未约定或无法确认时明确写“合同未约定”或“待业务确认”。

按用户身份切换立场：甲方/采购方/客户/被许可方时，以甲方保护逻辑审查；乙方/供应商/服务方/许可方时，以乙方保护逻辑审查；其他角色按照说明保护相应一方。

重点识别并按高风险优先级处理：单方变更、单方调价、预付款超过30%或付款未挂钩交付验收发票、验收标准缺失/默示验收/期限不足10个工作日、数据删除或AI训练/再识别/商业化/跨境、责任上限过低或数据/保密/IP/故意重大过失免责、无条件停服/限制账号、畸高退出成本、经营风险伪装不可抗力、对方所在地专属管辖、未经许可品牌宣传、合规与数据安全缺失。

每个风险必须可回链至精确合同原文或“【缺失该约定】”。高风险必须提供可直接写入合同的建议替换文本；同时给出谈判级别：must_modify、negotiable、internal_approval 或 prohibited，并在适当时给出最低可接受文本。

仅返回一个合法 JSON 对象，不要 Markdown。严格使用：
{
  "contract_type":"通用商务合同",
  "review_summary":"不超过500字的审查说明",
  "risks":[{
    "item":"检查项","level":"high|medium|low","original_text":"合同精确原文或【缺失该约定】","anchor_text":null,"insert_after_text":null,
    "clause_reference":"条款号或标题","risk":"风险及对我方影响","party_impact":"对我方影响","suggestion":"可直接写入合同的完整条文","minimum_acceptable_text":"最低可接受条文或空字符串","negotiation_level":"must_modify|negotiable|internal_approval|prohibited","laws":[]
  }],
  "deep_review":{
    "state":"completed","overall_conclusion":"可签|有条件可签|不建议签|待确认","executive_summary":"不超过500字","key_facts":[{"item":"付款","contract_term":"合同约定或合同未约定","conclusion":"审查结论/待确认"}],
    "missing_clauses":["缺失条款及简要原因"],
    "negotiation_items":[{"topic":"事项","target":"我方目标","minimum_acceptable":"最低可接受条件","owner":"法务/业务/IT安全/财务/管理层"}],
    "clarification_questions":["真正影响结论的问题，最多10条"],
    "settings_note":"未提供的可选偏好已按通用商业标准处理"
  },
  "coverage":[]
}"""


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
                        + "\n\n合同正文：\n" + _trim_contract_text(contract_text)
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=int(os.getenv("BAILIAN_DEEP_REVIEW_MAX_OUTPUT_TOKENS", "5000")),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **_json_mode_options(),
        )
        content = response.choices[0].message.content or ""
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
        payload = _parse_json_content(content)
        if not isinstance(payload, dict):
            raise ValueError("Deep review response must be a JSON object.")
        payload["filename"] = filename
        payload["contract_text"] = contract_text
        payload["review_scope"] = ["深度商业与谈判审查"]
        payload["review_method"] = "model"
        payload["review_duration_ms"] = round((perf_counter() - started_at) * 1000)
        payload = _normalize_risk_fields(payload)
        review = ReviewResponse(**payload)
        if review.deep_review is None or review.deep_review.state != "completed":
            raise ValueError("Deep review did not return a completed structured result.")
        if not review.review_summary.strip():
            raise ValueError("Deep review did not return a review summary.")
        review = _validate_contract_anchors(review, contract_text)
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
            detail="Deep review returned an incomplete result. Please retry the review; diagnostic details were recorded for administrators.",
        ) from exc
