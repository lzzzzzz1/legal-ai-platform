import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient

load_dotenv()
load_dotenv(dotenv_path=os.path.join(os.getcwd(), "backend", ".env"), override=True)

BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_DIMENSION = 1024


def _ensure_qdrant_no_proxy() -> None:
    additions = ["qdrant", "localhost", "127.0.0.1"]
    for key in ("NO_PROXY", "no_proxy"):
        existing = [item.strip() for item in os.getenv(key, "").split(",") if item.strip()]
        merged = existing + [item for item in additions if item not in existing]
        os.environ[key] = ",".join(merged)


def _create_embedding_client() -> OpenAI:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured.")

    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
        timeout=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120")),
    )


def _embed_query(query_text: str, embedding_client: Any | None = None) -> list[float]:
    client = embedding_client or _create_embedding_client()
    model = os.getenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    response = client.embeddings.create(model=model, input=[query_text], dimensions=EMBEDDING_DIMENSION)
    return response.data[0].embedding


def _create_qdrant_client() -> QdrantClient:
    _ensure_qdrant_no_proxy()
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))


def _law_label(payload: dict[str, Any]) -> str:
    law_name = str(payload.get("law_name", "未知法规"))
    article_no = str(payload.get("article_no", "未知条文"))
    return f"《{law_name}》{article_no}"


def retrieve_relevant_laws(
    query_text: str,
    top_k: int = 3,
    embedding_client: Any | None = None,
    qdrant_client: Any | None = None,
) -> list[dict[str, Any]]:
    query = query_text.strip()
    if not query:
        return []

    query_vector = _embed_query(query, embedding_client=embedding_client)
    client = qdrant_client or _create_qdrant_client()
    collection_name = os.getenv("QDRANT_COLLECTION", "legal_laws")
    results = client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
    )

    laws = []
    for result in results:
        payload = result.payload or {}
        laws.append(
            {
                "law_name": payload.get("law_name", ""),
                "article_no": payload.get("article_no", ""),
                "content": payload.get("content", ""),
                "score": result.score,
                "label": _law_label(payload),
            }
        )
    return laws


def format_laws_for_prompt(laws: list[dict[str, Any]]) -> str:
    if not laws:
        return "未检索到可用法条。请仅基于合同文本给出审查意见，并说明缺少可引用依据。"

    lines = []
    for index, law in enumerate(laws, start=1):
        label = law.get("label") or _law_label(law)
        content = str(law.get("content", "")).strip()
        lines.append(f"{index}. {label}：{content}")
    return "\n".join(lines)
