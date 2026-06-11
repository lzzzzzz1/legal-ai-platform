import json
import os

from dotenv import load_dotenv
from fastapi import HTTPException, status
from openai import OpenAI
from pydantic import ValidationError

from app.schemas.review import ReviewResponse

load_dotenv()

BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_DEFAULT_MODEL = "qwen-plus"
MAX_CONTRACT_CHARS = 60000

SYSTEM_PROMPT = (
    "你是一名资深合同审查律师。请分析合同条款，逐项检查："
    "合同份数、签订地点、联系人信息、税务条款。"
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


def _normalize_review_payload(payload: object) -> dict:
    if isinstance(payload, list):
        return {"risks": payload}

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object or an array of risks")

    if isinstance(payload.get("risks"), list):
        return payload

    for key in ("items", "results", "review"):
        value = payload.get(key)
        if isinstance(value, list):
            return {"risks": value}

    raise ValueError("payload must include a risks array")


def parse_review_response(content: str, filename: str) -> ReviewResponse:
    payload = json.loads(content)
    normalized_payload = _normalize_review_payload(payload)
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
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "请以 JSON 格式输出，格式必须是："
                        '{"risks":[{"item":"检查项","level":"high|medium|low",'
                        '"risk":"风险提示","suggestion":"修改建议"}]}。'
                        "level 只能使用 high、medium、low。\n\n"
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
