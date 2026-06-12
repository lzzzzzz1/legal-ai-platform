import json
import os

from dotenv import load_dotenv
from fastapi import HTTPException, status
from openai import OpenAI
from pydantic import ValidationError

from app.schemas.review import ReviewResponse
from app.services.rag_service import format_laws_for_prompt, retrieve_relevant_laws

load_dotenv()

BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_DEFAULT_MODEL = "qwen-max"
MAX_CONTRACT_CHARS = 60000
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
        return {"contract_type": None, "risks": payload}

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object or an array of risks")

    contract_type = payload.get("contract_type")

    if isinstance(payload.get("risks"), list):
        return {"contract_type": contract_type, "risks": payload["risks"]}

    for key in ("items", "results", "review"):
        value = payload.get(key)
        if isinstance(value, list):
            return {"contract_type": contract_type, "risks": value}

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
    if not isinstance(risks, list):
        return {**payload, "contract_type": _normalize_contract_type(payload.get("contract_type"))}

    normalized_risks = []
    for risk in risks:
        if not isinstance(risk, dict):
            normalized_risks.append(risk)
            continue

        normalized_risk = risk.copy()
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
    }


def parse_review_response(content: str, filename: str) -> ReviewResponse:
    payload = json.loads(content)
    normalized_payload = _normalize_review_payload(payload)
    # Remove contract_text from AI payload to prevent overriding
    # the backend-set value (which comes from the actual docx extraction).
    normalized_payload.pop("contract_text", None)
    normalized_payload = _normalize_risk_fields(normalized_payload)
    return ReviewResponse(filename=filename, **normalized_payload)


def review_contract_text(contract_text: str, filename: str) -> ReviewResponse:
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
    try:
        relevant_laws = retrieve_relevant_laws(_trim_contract_text(contract_text))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"RAG law retrieval failed: {exc}",
        ) from exc

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
                        "请以 JSON 格式输出，格式必须是："
                        '{"contract_type":"采购/供应合同|销售/服务合同|保密协议|通用商务合同",'
                        '"risks":[{"item":"检查项","level":"high|medium|low",'
                        '"original_text":"合同原文中的精确原句或【缺失该约定】",'
                        '"anchor_text":"定位相关条款的邻近标题、条款号或相邻原句，可为null",'
                        '"insert_after_text":"新增条款应插入其后的合同原文锚点或null",'
                        '"risk":"风险提示","suggestion":"修改建议",'
                        '"laws":["《法规名称》第XXX条"]}]}。'
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
                        f"参考法条：\n{law_context}\n\n"
                        f"合同文本：\n{_trim_contract_text(contract_text)}"
                    ),
                },
            ],
            response_format=_response_format(),
            temperature=0.2,
        )
    except Exception as exc:
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
        return parse_review_response(content=content, filename=filename)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bailian returned an invalid review payload: {exc}",
        ) from exc
