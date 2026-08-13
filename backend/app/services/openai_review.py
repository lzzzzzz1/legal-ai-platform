import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException, status
from openai import OpenAI
from pydantic import ValidationError

from app.schemas.review import LawReference, ReviewCoverage, ReviewResponse
from app.services.rag_service import format_laws_for_prompt, retrieve_relevant_laws
from app.services.rule_review import RULE_TOPICS, run_rule_fallback
from app.services.document_preflight import PREFLIGHT_SCOPE, run_document_preflight
from app.services.review_verifier import verify_high_risk_findings
from app.services.consistency_review import run_consistency_checks

load_dotenv()

BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_DEFAULT_MODEL = "qwen-max"
MAX_CONTRACT_CHARS = int(os.getenv("MAX_CONTRACT_CHARS", "40000"))
REVIEW_SEGMENT_CHARS = int(os.getenv("REVIEW_SEGMENT_CHARS", "18000"))
REVIEW_REPAIR_TIMEOUT_SECONDS = float(os.getenv("REVIEW_REPAIR_TIMEOUT_SECONDS", "20"))
REVIEW_JSON_RETRY_MAX_CONTRACT_CHARS = int(os.getenv("REVIEW_JSON_RETRY_MAX_CONTRACT_CHARS", "12000"))
REVIEW_SCOPE = [PREFLIGHT_SCOPE, *(topic.name for topic in RULE_TOPICS)]
SUPPORTED_CONTRACT_TYPES = {
    "采购/供应合同": "采购/供应合同",
    "采购合同": "采购/供应合同",
    "供应合同": "采购/供应合同",
    "销售/服务合同": "销售/服务合同",
    "销售合同": "销售/服务合同",
    "服务合同": "销售/服务合同",
    "保密协议": "保密协议",
    "nda": "保密协议",
    "通用商务合同": "通用商务合同",
    "通用合同": "通用商务合同",
}

SYSTEM_PROMPT = (
    "你是一名资深企业法务律师，负责审阅企业间商业合同。"
    "你必须先识别合同类型，并且 contract_type 只能输出以下四个固定值之一："
    "采购/供应合同、销售/服务合同、保密协议、通用商务合同。"
    "如果无法稳定判断，也必须输出通用商务合同。"
    "识别完成后，再按对应类型执行审查："
    "基础质量与合同框架由系统本地规则预检，不需要生成风险项；"
    "所有合同均须覆盖主体与签约权限、合同成立与效力、标的与价格、付款与发票、交付与验收、质量与售后、违约与责任、解除与终止、知识产权、保密与数据、合规与许可、通知与送达、争议解决、附件与文本一致性；"
    "采购/供应合同重点审查付款账期与比例、发票与税费、交付与验收、延期交付违约、质量保证/售后、知识产权归属、责任限制；"
    "销售/服务合同重点审查服务范围与交付标准、付款节点、SLA/违约责任、客户数据与保密、知识产权与成果归属、终止与续约；"
    "保密协议重点审查保密信息定义、保密义务范围、例外情形、保密期限、资料返还/销毁、违约责任与救济；"
    "通用商务合同重点审查通知条款、税务与开票、争议解决、合同份数与签署、适用法律、完整协议/转让/不可抗力。"
    "你必须结合提供的参考法条提出修改建议。"
    "suggestion 字段只能填写可直接写回合同正文的条款文本本身，不得加入“建议修改如下”“引用某法第X条”等解释性语句。"
    "法律法规名称及条文号只能放在 laws 数组中，不得混入 suggestion。"
    "每个风险项必须包含 original_text 字段："
    "  - 如果该条款在合同中存在对应文字，original_text 必须是合同原文中可精确定位的完整原句或短语，"
    "    不得改写、增删标点符号、空格或换行，不得翻译，不得概括。"
    "  - 如果该条款在合同中完全缺失（即合同根本没有提及该内容），"
    "    original_text 必须设为固定值：【缺失该约定】，并尽量提供 insert_after_text。"
    "anchor_text 应尽量提供与风险相关的邻近标题、条款号或相邻原句，便于前端定位。"
    "insert_after_text 必须是合同中真实存在的完整原句或标题，用于定位新增条款插入位置；"
    "如果无法判断插入位置，可返回 null。"
    "只输出 JSON，不要输出 Markdown。"
)


def _response_format() -> dict[str, str]:
    return {"type": "json_object"}


def _model_json_mode_enabled() -> bool:
    """Return whether the provider can reliably enforce OpenAI JSON mode.

    Some OpenAI-compatible local gateways advertise ``json_object`` but emit a
    token-fragmented pseudo JSON response when it is enabled.  The prompt and
    the server-side parser still enforce the schema, so JSON mode is opt-in
    rather than silently corrupting an otherwise usable review.
    """
    return os.getenv("BAILIAN_RESPONSE_FORMAT", "off").strip().lower() in {
        "json",
        "json_object",
        "on",
        "true",
        "1",
    }


def _json_mode_options() -> dict[str, object]:
    return {"response_format": _response_format()} if _model_json_mode_enabled() else {}


def _trim_contract_text(contract_text: str) -> str:
    if len(contract_text) <= MAX_CONTRACT_CHARS:
        return contract_text

    return (
        contract_text[:MAX_CONTRACT_CHARS]
        + "\n\n[合同文本过长，已截取前 60000 个字符用于本次 MVP 审查。]"
    )


def _infer_contract_language(contract_text: str) -> str:
    sample = contract_text[:4000]
    if not sample.strip():
        return "unknown"

    english_chars = sum(1 for char in sample if char.isascii() and char.isalpha())
    chinese_chars = sum(1 for char in sample if "\u4e00" <= char <= "\u9fff")

    if english_chars > chinese_chars * 2 and english_chars > 200:
        return "english"
    if chinese_chars > english_chars:
        return "chinese"
    if english_chars > 0 and chinese_chars > 0:
        return "bilingual"
    return "unknown"


def _normalize_review_payload(payload: object) -> dict:
    if isinstance(payload, list):
        return {"contract_type": None, "risks": payload, "coverage": [], "review_scope": [], "warnings": []}

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object or an array of risks")

    contract_type = payload.get("contract_type")

    if isinstance(payload.get("risks"), list):
        return {
            "contract_type": contract_type,
            "risks": payload["risks"],
            "review_summary": payload.get("review_summary", ""),
            "coverage": payload.get("coverage", []),
            "review_scope": payload.get("review_scope", []),
            "warnings": payload.get("warnings", []),
            "deep_review": payload.get("deep_review"),
        }

    for key in ("items", "results", "review"):
        value = payload.get(key)
        if isinstance(value, list):
            return {"contract_type": contract_type, "risks": value, "coverage": [], "review_scope": [], "warnings": [], "deep_review": payload.get("deep_review")}

    raise ValueError("payload must include a risks array")


def _normalize_contract_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    mapped = SUPPORTED_CONTRACT_TYPES.get(cleaned)
    if mapped:
        return mapped

    lowered = cleaned.lower()
    mapped = SUPPORTED_CONTRACT_TYPES.get(lowered)
    if mapped:
        return mapped

    if "采购" in cleaned or "供应" in cleaned:
        return "采购/供应合同"
    if "销售" in cleaned or "服务" in cleaned:
        return "销售/服务合同"
    if "保密" in cleaned or "nda" in lowered:
        return "保密协议"
    return "通用商务合同"


def _normalize_risk_fields(payload: dict) -> dict:
    risks = payload.get("risks")
    def normalize_coverage(value: object) -> list[dict]:
        """Accept concise model coverage labels without weakening the API schema."""
        if not isinstance(value, list):
            return []

        normalized: list[dict] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                normalized.append({
                    "topic": entry.strip(),
                    "status": "checked",
                    "evidence": None,
                    "method": "model",
                })
            elif isinstance(entry, dict):
                item = entry.copy()
                if "topic" not in item:
                    for alias in ("检查项", "审查项", "范围", "item"):
                        if isinstance(item.get(alias), str):
                            item["topic"] = item[alias]
                            break
                if isinstance(item.get("topic"), str) and item["topic"].strip():
                    item["topic"] = item["topic"].strip()
                    item["status"] = item.get("status") if item.get("status") in {"checked", "missing", "uncertain"} else "uncertain"
                    item["method"] = item.get("method") if item.get("method") in {"model", "rule", "combined"} else "model"
                    normalized.append(item)
        return normalized

    normalized_coverage = normalize_coverage(payload.get("coverage", []))
    if not isinstance(risks, list):
        return {
            **payload,
            "contract_type": _normalize_contract_type(payload.get("contract_type")),
            "coverage": normalized_coverage,
        }

    normalized_risks = []
    for risk in risks:
        if not isinstance(risk, dict):
            normalized_risks.append(risk)
            continue

        normalized_risk = risk.copy()
        aliases = {
            "item": ("检查项", "检查项目", "项目", "审查项"),
            "level": ("风险等级", "等级"),
            "original_text": ("原文", "定位原文", "合同原文"),
            "risk": ("风险提示", "风险说明"),
            "suggestion": ("修改建议", "建议", "建议补充条款"),
            "laws": ("法规依据", "参考法条", "法律依据"),
        }
        for target, candidates in aliases.items():
            if target not in normalized_risk:
                for candidate in candidates:
                    if candidate in normalized_risk:
                        normalized_risk[target] = normalized_risk[candidate]
                        break

        level_aliases = {"高风险": "high", "中风险": "medium", "低风险": "low", "高": "high", "中": "medium", "低": "low"}
        if isinstance(normalized_risk.get("level"), str):
            normalized_risk["level"] = level_aliases.get(normalized_risk["level"].strip(), normalized_risk["level"])
        laws = normalized_risk.get("laws")
        if isinstance(laws, str):
            normalized_risk["laws"] = [laws]
        elif laws is None:
            normalized_risk["laws"] = []

        normalized_risks.append(normalized_risk)

    return {
        **payload,
        "contract_type": _normalize_contract_type(payload.get("contract_type")),
        "risks": normalized_risks,
        "coverage": normalized_coverage,
    }


def _validate_law_evidence(review: ReviewResponse, relevant_laws: list[dict]) -> ReviewResponse:
    """Flag citations that cannot be matched to the retrieved, verified law set."""
    references = [str(law.get("label", "")).replace(" ", "") for law in relevant_laws]
    warnings = list(review.warnings)
    unsupported = 0
    missing = 0
    for risk in review.risks:
        risk.law_references = []
        if not risk.laws:
            risk.evidence_status = "needs_manual_review"
            missing += 1
            continue
        citation_verified = True
        verified_labels = []
        for citation in risk.laws:
            normalized = str(citation).replace(" ", "")
            matched_laws = [
                law
                for law, reference in zip(relevant_laws, references)
                if normalized and (normalized in reference or reference in normalized)
            ]
            if not matched_laws:
                citation_verified = False
                unsupported += 1
            for law in matched_laws:
                verified_labels.append(str(law.get("label", "")))
                reference = LawReference(
                    label=str(law.get("label", "")),
                    official_url=law.get("official_url"),
                    authority=law.get("authority"),
                    effectiveness_status=law.get("effectiveness_status"),
                )
                if reference not in risk.law_references:
                    risk.law_references.append(reference)
        risk.laws = list(dict.fromkeys(label for label in verified_labels if label))
        risk.evidence_status = (
            "verified"
            if citation_verified
            and references
            and all(
                reference.official_url
                and reference.effectiveness_status == "effective"
                for reference in risk.law_references
            )
            else "needs_manual_review"
        )

    if not relevant_laws and review.risks:
        warnings.append("未检索到可核验的有效法规依据；本次风险结论需人工复核，不能视为已有法律依据支持。")
    if missing:
        warnings.append(f"有 {missing} 条风险未提供法规依据，已标记为需人工核验。")
    if unsupported:
        warnings.append(f"有 {unsupported} 条法规引用无法在本次检索结果中匹配，已标记为需人工核验。")
    review.warnings = list(dict.fromkeys(warnings))
    return review


def _validate_contract_anchors(review: ReviewResponse, contract_text: str) -> ReviewResponse:
    """Ensure every model finding can be located in the uploaded contract."""
    missing_original = 0
    missing_anchor = 0
    missing_marker = "【缺失该约定】"
    for risk in review.risks:
        original = risk.original_text.strip()
        if original and original != missing_marker and original not in contract_text:
            risk.evidence_status = "needs_manual_review"
            missing_original += 1
        anchor = (risk.insert_after_text or risk.anchor_text or "").strip()
        if anchor and anchor not in contract_text:
            risk.evidence_status = "needs_manual_review"
            missing_anchor += 1

    warnings = list(review.warnings)
    if missing_original:
        warnings.append(f"有 {missing_original} 条风险的原文定位无法在合同中找到，已标记为需人工复核。")
    if missing_anchor:
        warnings.append(f"有 {missing_anchor} 条风险的插入锚点无法在合同中找到，已标记为需人工复核。")
    review.warnings = list(dict.fromkeys(warnings))
    return review


def _audit_review(*, filename: str, raw_response: str, duration_ms: int, status: str, risk_count: int, error: str | None = None) -> None:
    """Write an append-only, local audit record without logging API keys or contract text."""
    path = Path(os.getenv("REVIEW_AUDIT_LOG", "logs/reviews.jsonl"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    redact = os.getenv("REVIEW_AUDIT_REDACT", "true").lower() in {"1", "true", "yes"}
    audited_response = _redact_audit_text(raw_response) if redact else raw_response
    record = {
        "review_id": str(uuid4()),
        "filename": filename,
        "duration_ms": duration_ms,
        "status": status,
        "risk_count": risk_count,
        "raw_model_response": audited_response,
        "raw_response_redacted": redact,
    }
    if error:
        record["error"] = error
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Auditing must never make a successful review fail.
        pass


def _redact_audit_text(value: str) -> str:
    """Remove common personal/contact identifiers before writing review logs."""
    value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
    value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[PHONE]", value)
    value = re.sub(r"(?<!\d)\d{17}[0-9Xx](?!\d)", "[ID]", value)
    return value


def _finalize_review(
    review: ReviewResponse,
    contract_text: str,
    duration_ms: int,
    selected_scope: list[str] | None = None,
) -> ReviewResponse:
    review = _validate_contract_anchors(review, contract_text)
    allowed_scope = ([PREFLIGHT_SCOPE] if selected_scope is None or PREFLIGHT_SCOPE in selected_scope else []) + [
        topic.name for topic in RULE_TOPICS
        if selected_scope is None or topic.name in selected_scope
    ]
    allowed_scope_set = set(allowed_scope)
    if selected_scope is not None:
        review.risks = [risk for risk in review.risks if risk.item in allowed_scope_set]
    fallback_risks, rule_coverage = run_rule_fallback(contract_text, allowed_scope)
    existing_topics = {risk.item.strip() for risk in review.risks}
    for fallback_risk in fallback_risks:
        if fallback_risk.item not in existing_topics:
            review.risks.append(fallback_risk)

    model_coverage = {item.topic: item for item in review.coverage}
    merged_coverage = []
    coverage_conflicts = []
    for item in rule_coverage:
        model_item = model_coverage.get(item.topic)
        if model_item and model_item.status != item.status:
            coverage_conflicts.append(
                f"{item.topic}（模型：{model_item.status}，规则：{item.status}）"
            )
        if model_item and model_item.status == "checked" and item.status == "checked":
            item.status = "checked"
            item.method = "combined"
            item.evidence = model_item.evidence or item.evidence
        elif model_item and model_item.status == "uncertain":
            item.status = "uncertain"
            item.method = "combined"
            item.evidence = model_item.evidence or item.evidence
        merged_coverage.append(item)

    warnings = list(review.warnings)
    if coverage_conflicts:
        warnings.append(
            "模型审查与规则检查存在冲突，已要求人工复核：" + "；".join(coverage_conflicts)
        )
    has_model_summary = bool(review.review_summary.strip())
    summary = review.review_summary.strip()
    if not has_model_summary:
        summary = "模型未提供可验证的审查说明；空风险列表不等同于合同无风险。"
        warnings.append("模型未返回审查说明，需由法务人员复核审查覆盖范围。")
    if not review.risks and not all(item.status == "checked" for item in merged_coverage):
        warnings.append("关键条款存在未检出或不确定项，系统未将本次结果判定为无风险。")

    review.contract_text = contract_text
    review.review_scope = allowed_scope
    review.coverage = merged_coverage
    review.preflight_checks = run_document_preflight(contract_text) if PREFLIGHT_SCOPE in allowed_scope_set else []
    preflight_warnings = [check for check in review.preflight_checks if check.status == "warning"]
    if preflight_warnings:
        review.warnings.append(
            f"基础质量预检发现 {len(preflight_warnings)} 项待确认内容；请先核对标点、文字和合同框架后再处理实质条款。"
        )
    review.review_summary = summary
    review.warnings = list(dict.fromkeys(warnings))
    review.consistency_checks = run_consistency_checks(contract_text, allowed_scope)
    consistency_warnings = [
        check for check in review.consistency_checks if check.status == "warning"
    ]
    if consistency_warnings:
        review.warnings.append(
            "合同内部一致性检查发现需人工确认项："
            + "、".join(check.check for check in consistency_warnings)
        )
    review = verify_high_risk_findings(review)
    has_evidence_warning = any(
        "法规依据" in warning
        or "法规引用" in warning
        or "法律依据" in warning
        or "原文定位" in warning
        or "插入锚点" in warning
        or "模型审查与规则检查存在冲突" in warning
        or "高风险项二次复核未通过" in warning
        for warning in review.warnings
    )
    review.review_status = (
        "complete"
        if has_model_summary and all(item.status == "checked" for item in merged_coverage) and not has_evidence_warning
        else "partial"
    )
    if any(risk.evidence_status != "verified" for risk in review.risks) or consistency_warnings or preflight_warnings:
        review.review_status = "partial"
    review.manual_review_required = review.review_status != "complete"
    review.review_method = "combined"
    if not review.risks and not has_model_summary:
        review.review_status = "needs_manual_review"
    review.review_duration_ms = duration_ms
    return review


def _parse_json_content(content: str) -> object:
    cleaned = content.strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(cleaned[index:])
                return payload
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON object or array was found in model output.")


def parse_review_response(content: str, filename: str) -> ReviewResponse:
    payload = _parse_json_content(content)
    normalized_payload = _normalize_review_payload(payload)
    # Remove contract_text from AI payload to prevent overriding
    # the backend-set value (which comes from the actual docx extraction).
    normalized_payload.pop("contract_text", None)
    normalized_payload = _normalize_risk_fields(normalized_payload)
    return ReviewResponse(filename=filename, **normalized_payload)


def _is_uninformative_model_response(review: ReviewResponse) -> bool:
    """Treat an empty, unexplained response as unusable rather than risk-free."""
    return not review.risks and not review.review_summary.strip()


def _retry_review_with_compact_json(
    *,
    api_key: str,
    model: str,
    contract_text: str,
    filename: str,
    selected_scope: list[str],
) -> tuple[ReviewResponse, str]:
    """Retry the original contract with a compact schema after malformed output.

    This is deliberately not a JSON "repair" prompt: severely corrupted output
    has no dependable structure to repair. Re-reviewing the original text with
    fewer instructions is materially more reliable on OpenAI-compatible local
    inference services.
    """
    retry_client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
        timeout=REVIEW_REPAIR_TIMEOUT_SECONDS,
    )
    excerpt = contract_text[:REVIEW_JSON_RETRY_MAX_CONTRACT_CHARS]
    response = retry_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a contract reviewer. Return one valid JSON object only. "
                    "Do not use Markdown, explanations, or thinking text."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Review only these topics: " + "、".join(selected_scope) + "\n"
                    "Return exactly this shape: "
                    '{"contract_type":"通用商务合同","review_summary":"简短审查说明",'
                    '"risks":[{"item":"检查项","level":"high|medium|low",'
                    '"original_text":"合同原文或【缺失该约定】","risk":"风险说明",'
                    '"suggestion":"可直接写入合同的条款","laws":[]}]}\n'
                    "Every risk must contain all fields. If no evidence-backed risk is found, return "
                    '{"contract_type":"通用商务合同","review_summary":"已按所选范围审查，未形成可验证风险。",'
                    '"risks":[]}.\n合同文本：\n' + excerpt
                ),
            },
        ],
        temperature=0,
        max_tokens=min(int(os.getenv("BAILIAN_MAX_OUTPUT_TOKENS", "2048")), 2048),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **_json_mode_options(),
    )
    content = response.choices[0].message.content or ""
    if not content:
        raise ValueError("compact retry returned an empty response")
    return parse_review_response(content=content, filename=filename), content


def _review_contract_segment(
    contract_text: str,
    filename: str,
    selected_scope: list[str] | None = None,
) -> ReviewResponse:
    model_scope_topics = [
        topic for topic in (REVIEW_SCOPE if selected_scope is None else selected_scope)
        if topic != PREFLIGHT_SCOPE
    ]
    if not model_scope_topics:
        return ReviewResponse(
            filename=filename,
            risks=[],
            review_summary="已完成基础质量与合同框架检查；未请求条款实质审查。",
            review_scope=selected_scope or [PREFLIGHT_SCOPE],
            review_status="complete",
            manual_review_required=False,
            review_method="rule",
        )

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHSCOPE_API_KEY is not configured.",
        )

    timeout_seconds = float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120"))
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
        timeout=timeout_seconds,
    )
    model = os.getenv("BAILIAN_MODEL", BAILIAN_DEFAULT_MODEL)
    started_at = perf_counter()
    retrieval_warning = ""
    try:
        scope_topics = model_scope_topics
        scope_hint = "、".join(scope_topics)
        retrieval_query = _trim_contract_text(contract_text) + f"\n本次审查范围：{scope_hint}"
        try:
            relevant_laws = retrieve_relevant_laws(
                retrieval_query,
                review_topics=scope_topics,
            )
        except TypeError as exc:
            # Keep compatibility with injected/mock retrievers using the old signature.
            if "review_topics" not in str(exc):
                raise
            relevant_laws = retrieve_relevant_laws(retrieval_query)
    except Exception as exc:
        # Law retrieval is an enrichment step. The contract review should still
        # be available when Qdrant or the embedding provider is temporarily down.
        relevant_laws = []
        retrieval_warning = f"法规检索失败：{exc}；本次结论需人工复核。"

    law_context = format_laws_for_prompt(relevant_laws)
    contract_language = _infer_contract_language(contract_text)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                        "content": (
                        f"本次仅审查以下范围：{'、'.join(scope_topics)}\n"
                        f"review_scope 必须原样返回以上范围，不得添加未勾选项目。\n"
                        "请以 JSON 格式输出，格式必须是："
                        '{"contract_type":"采购/供应合同|销售/服务合同|保密协议|通用商务合同",'
                        '"risks":[{"item":"检查项","level":"high|medium|low",'
                        '"original_text":"合同原文中的精确原句或【缺失该约定】",'
                        '"anchor_text":"定位相关条款的邻近标题、条款号或相邻原句，可为null",'
                        '"insert_after_text":"新增条款应插入其后的合同原文锚点或null",'
                        '"risk":"风险提示","suggestion":"修改建议",'
                        '"laws":["《法规名称》第XXX条"]}],'
                        '"review_summary":"基于合同原文的审查结论",'
                        '"review_scope":["付款与发票","交付与验收","违约与责任","知识产权"],'
                        '"coverage":[{"topic":"付款与发票","status":"checked|missing|uncertain","evidence":"合同原文依据"}]}'
                        "contract_type 必须是四个固定值之一，不得自由发挥。"
                        "level 只能使用 high、medium、low。"
                        "original_text 必须逐字复制合同文本中的对应内容，标点、空格和换行必须保持一致。"
                        "anchor_text 应优先返回相关章节标题、条款号或邻近原句。"
                        "若 original_text 为【缺失该约定】，insert_after_text 必须优先选择合同中相关章节标题或相邻条款原文。"
                        "laws 必须列出本条建议引用的法规名称及条文号。"
                        "risk 字段用中文概述风险。"
                        "suggestion 必须只包含可直接插入合同的条款正文，不得包含解释、提示语、项目符号或法律引用说明。"
                        f"当前合同语言判断为：{contract_language}。"
                        "若合同以英文为主，suggestion 必须使用英文合同条款语言；"
                        "若合同以中文为主，suggestion 必须使用中文合同条款语言；"
                        "若合同为中英混合，suggestion 必须跟随 original_text 或 insert_after_text 所在章节的语言。\n\n"
                        "法规引用约束：laws 只能从下方参考法条中原样选择，不能凭记忆补写法规名称或条文号；如果下方没有足够依据，laws 必须返回空数组，并在 review_summary 或 warnings 中说明需人工核验。\n"
                        f"参考法条：\n{law_context}\n\n"
                        f"合同文本：\n{_trim_contract_text(contract_text)}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=int(os.getenv("BAILIAN_MAX_OUTPUT_TOKENS", "2048")),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **_json_mode_options(),
        )
    except Exception as exc:
        _audit_review(
            filename=filename,
            raw_response="",
            duration_ms=round((perf_counter() - started_at) * 1000),
            status="request_failed_rule_fallback",
            risk_count=0,
            error=str(exc),
        )
        fallback_enabled = os.getenv("REVIEW_RULE_FALLBACK_ON_MODEL_ERROR", "true").lower() in {
            "1",
            "true",
            "yes",
        }
        if fallback_enabled:
            return ReviewResponse(
                filename=filename,
                risks=[],
                review_summary="模型服务当前不可连接，已完成所选审查范围的规则兜底检查；结果需要人工复核。",
                review_scope=selected_scope or REVIEW_SCOPE,
                warnings=[f"模型审查暂时不可用：{exc}；本次已切换为规则兜底审查。"],
                review_status="needs_manual_review",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bailian review request failed: {exc}",
        ) from exc

    content = response.choices[0].message.content
    if not content:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Bailian returned an empty review response.",
        )

    try:
        parsed = parse_review_response(content=content, filename=filename)
        if _is_uninformative_model_response(parsed):
            raise ValueError("model returned an empty risks list without a review summary")
        if retrieval_warning:
            parsed.warnings.append(retrieval_warning)
        parsed = _validate_law_evidence(parsed, relevant_laws)
        _audit_review(
            filename=filename,
            raw_response=content,
            duration_ms=round((perf_counter() - started_at) * 1000),
            status="model_response",
            risk_count=len(parsed.risks),
        )
        return parsed
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        repaired_content = ""
        try:
            parsed, repaired_content = _retry_review_with_compact_json(
                api_key=api_key,
                model=model,
                contract_text=contract_text,
                filename=filename,
                selected_scope=scope_topics,
            )
            if retrieval_warning:
                parsed.warnings.append(retrieval_warning)
            parsed = _validate_law_evidence(parsed, relevant_laws)
            _audit_review(
                filename=filename,
                raw_response=content + "\n[repaired]\n" + repaired_content,
                duration_ms=round((perf_counter() - started_at) * 1000),
                status="compact_retry_response",
                risk_count=len(parsed.risks),
            )
            return parsed
        except Exception as repair_exc:
            _audit_review(
                filename=filename,
                raw_response=content + "\n[repair_failed]\n" + repaired_content,
                duration_ms=round((perf_counter() - started_at) * 1000),
                status="invalid_response_no_json" if "No valid JSON" in str(exc) else "invalid_response",
                risk_count=0,
                error=f"initial={exc}; repair={repair_exc}",
            )
            # Do not turn a malformed model response into a user-facing 500.
            # _finalize_review will add deterministic rule findings and mark the
            # result as partial/needs_manual_review.
            return ReviewResponse(
                filename=filename,
                risks=[],
                warnings=["模型返回的数据结构不完整，已启用规则兜底并要求人工复核。"],
            )


def _split_contract_text(contract_text: str) -> list[str]:
    """Split on paragraph boundaries while keeping each model request bounded."""
    target = max(1000, REVIEW_SEGMENT_CHARS)
    paragraphs = contract_text.splitlines(keepends=True)
    segments: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > target:
            if current:
                segments.append(current)
                current = ""
            for start in range(0, len(paragraph), target):
                segments.append(paragraph[start : start + target])
            continue

        if current and len(current) + len(paragraph) > target:
            segments.append(current)
            current = ""
        current += paragraph

    if current:
        segments.append(current)
    return segments or [contract_text]


def review_contract_text(
    contract_text: str,
    filename: str,
    selected_scope: list[str] | None = None,
) -> ReviewResponse:
    """Review every contract segment and merge risks back into one response."""
    started_at = perf_counter()
    if selected_scope is not None and not [topic for topic in selected_scope if topic != PREFLIGHT_SCOPE]:
        return _finalize_review(
            ReviewResponse(
                filename=filename,
                risks=[],
                review_summary="已完成基础质量与合同框架检查；未请求条款实质审查。",
                review_scope=selected_scope,
                review_status="complete",
                manual_review_required=False,
                review_method="rule",
            ),
            contract_text,
            round((perf_counter() - started_at) * 1000),
            selected_scope,
        )
    segments = _split_contract_text(contract_text)
    if len(segments) == 1:
        return _finalize_review(
            _review_contract_segment(contract_text, filename, selected_scope),
            contract_text,
            round((perf_counter() - started_at) * 1000),
            selected_scope,
        )

    worker_count = min(3, len(segments))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="contract-review") as executor:
        if selected_scope is None:
            segment_reviews = list(executor.map(
                lambda segment: _review_contract_segment(segment, filename),
                segments,
            ))
        else:
            segment_reviews = list(executor.map(
                lambda segment: _review_contract_segment(segment, filename, selected_scope),
                segments,
            ))
    merged_risks = []
    risk_index: dict[tuple[str, str], int] = {}
    merged_warnings: list[str] = []
    segment_coverage: dict[str, ReviewCoverage] = {}
    level_rank = {"high": 0, "medium": 1, "low": 2}

    def normalize_key(value: str) -> str:
        return " ".join(value.split()).casefold()

    for segment_review in segment_reviews:
        merged_warnings.extend(segment_review.warnings)
        for coverage in segment_review.coverage:
            current = segment_coverage.get(coverage.topic)
            if current is None:
                segment_coverage[coverage.topic] = coverage
            elif coverage.status != "checked":
                segment_coverage[coverage.topic] = coverage
        for risk in segment_review.risks:
            key = (
                normalize_key(risk.item),
                normalize_key(risk.original_text),
            )
            existing_index = risk_index.get(key)
            if existing_index is None:
                risk_index[key] = len(merged_risks)
                merged_risks.append(risk)
                continue

            existing = merged_risks[existing_index]
            if level_rank[risk.level] < level_rank[existing.level]:
                existing.level = risk.level
            existing.laws = list(dict.fromkeys(existing.laws + risk.laws))
            for reference in risk.law_references:
                if reference not in existing.law_references:
                    existing.law_references.append(reference)
            if risk.evidence_status == "verified":
                existing.evidence_status = "verified"
            if risk.source != existing.source:
                existing.source = "combined"
            if len(risk.suggestion) > len(existing.suggestion):
                existing.suggestion = risk.suggestion
            existing.anchor_text = existing.anchor_text or risk.anchor_text
            existing.insert_after_text = existing.insert_after_text or risk.insert_after_text

    contract_type = next(
        (review.contract_type for review in segment_reviews if review.contract_type),
        None,
    )
    merged = ReviewResponse(
        filename=filename,
        contract_type=contract_type,
        contract_text=contract_text,
        risks=merged_risks,
        warnings=list(dict.fromkeys(merged_warnings)),
        coverage=list(segment_coverage.values()),
    )
    return _finalize_review(
        merged,
        contract_text,
        round((perf_counter() - started_at) * 1000),
        selected_scope,
    )
