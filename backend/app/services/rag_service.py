import os
import time
from copy import deepcopy
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.services.reranker import rerank_documents

load_dotenv()
load_dotenv(dotenv_path=os.path.join(os.getcwd(), "backend", ".env"), override=True)

BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_DIMENSION = 1024
_RAG_CACHE: dict[tuple[str, int, str, str], tuple[float, list[dict[str, Any]]]] = {}


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
        base_url=os.getenv(
            "BAILIAN_EMBEDDING_BASE_URL",
            os.getenv("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
        ),
        timeout=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120")),
    )


def _embed_query(query_text: str, embedding_client: Any | None = None) -> list[float]:
    client = embedding_client or _create_embedding_client()
    model = os.getenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    response = client.embeddings.create(model=model, input=[query_text])
    vector = response.data[0].embedding
    if len(vector) != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Embedding dimension mismatch: expected {EMBEDDING_DIMENSION}, got {len(vector)}"
        )
    return vector


def _create_qdrant_client() -> QdrantClient:
    _ensure_qdrant_no_proxy()
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )


def _effective_law_filter() -> Filter | None:
    """Exclude law records whose current effectiveness has not been verified."""
    enabled = os.getenv("RAG_REQUIRE_EFFECTIVE_LAWS", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    if not enabled:
        return None
    return Filter(
        must=[
            FieldCondition(
                key="effectiveness_status",
                match=MatchValue(value="effective"),
            )
        ]
    )


def _cached_laws(key: tuple[str, int, str, str]) -> list[dict[str, Any]] | None:
    ttl = float(os.getenv("RAG_CACHE_TTL_SECONDS", "60"))
    cached = _RAG_CACHE.get(key)
    if not cached:
        return None
    timestamp, laws = cached
    if time.monotonic() - timestamp > ttl:
        _RAG_CACHE.pop(key, None)
        return None
    return deepcopy(laws)


def _store_cached_laws(key: tuple[str, int, str, str], laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if float(os.getenv("RAG_CACHE_TTL_SECONDS", "60")) <= 0:
        return laws
    _RAG_CACHE[key] = (time.monotonic(), deepcopy(laws))
    return laws


def _focused_query(query: str, review_topics: list[str] | None = None) -> str:
    """Add a small domain hint so retrieval favors applicable official rules."""
    focus_terms: list[str] = []
    if any(term in query for term in ("采购", "供应", "供货", "交货", "验收")):
        focus_terms.extend(("采购供应合同", "质量验收", "货物交付", "付款"))
    if any(term in query for term in ("销售", "服务", "项目", "实施", "系统")):
        focus_terms.extend(("销售服务合同", "服务交付", "验收", "违约责任"))
    if any(term in query for term in ("保密", "商业秘密", "披露", "个人信息")):
        focus_terms.extend(("保密协议", "商业秘密", "个人信息保护", "数据处理"))
    if review_topics:
        focus_terms.extend(review_topics)
    if focus_terms:
        return f"{query}\n适用法规检索重点：{'、'.join(dict.fromkeys(focus_terms))}"
    return query


def _law_label(payload: dict[str, Any]) -> str:
    law_name = str(payload.get("law_name", "未知法规"))
    article_no = str(payload.get("article_no", "未知条文"))
    return f"《{law_name}》{article_no}"


def retrieve_relevant_laws(
    query_text: str,
    top_k: int = 3,
    review_topics: list[str] | None = None,
    embedding_client: Any | None = None,
    qdrant_client: Any | None = None,
) -> list[dict[str, Any]]:
    query = _focused_query(query_text.strip(), review_topics)
    if not query:
        return []

    collection_name = os.getenv("QDRANT_COLLECTION", "legal_laws")
    effectiveness_mode = "effective-only" if _effective_law_filter() is not None else "all"
    topic_key = "|".join(review_topics or [])
    cache_key = (query, top_k, collection_name, f"{effectiveness_mode}:{topic_key}")
    cache_allowed = embedding_client is None and qdrant_client is None
    if cache_allowed:
        cached = _cached_laws(cache_key)
        if cached is not None:
            return cached
    query_vector = _embed_query(query, embedding_client=embedding_client)
    client = qdrant_client or _create_qdrant_client()
    rerank_enabled = os.getenv("RERANK_ENABLED", "true").lower() in {"1", "true", "yes"}
    recall_k = (
        max(top_k, int(os.getenv("RAG_RECALL_K", str(top_k * 4))))
        if rerank_enabled
        else top_k
    )
    search_kwargs = {
        "collection_name": collection_name,
        "query_vector": query_vector,
        "limit": recall_k,
        "with_payload": True,
    }
    law_filter = _effective_law_filter()
    # Keep lightweight fake clients usable in unit tests while production Qdrant
    # receives the verification filter.
    if law_filter is not None and isinstance(client, QdrantClient):
        search_kwargs["query_filter"] = law_filter
    results = client.search(**search_kwargs)

    laws = []
    for result in results:
        payload = result.payload or {}
        law = {
                "law_name": payload.get("law_name", ""),
                "article_no": payload.get("article_no", ""),
                "content": payload.get("content", ""),
                "score": result.score,
                "label": _law_label(payload),
            }
        for key in (
            "jurisdiction",
            "legal_level",
            "promulgated_date",
            "effective_date",
            "effectiveness_status",
            "last_verified_at",
            "official_url",
            "authority",
            "source_sha256",
            "risk_topics",
            "verification_policy",
        ):
            if payload.get(key) not in (None, ""):
                law[key] = payload[key]
        laws.append(law)

    require_official = os.getenv("RAG_REQUIRE_OFFICIAL_SOURCE", "true").lower() in {"1", "true", "yes"}
    if require_official:
        laws = [
            law for law in laws
            if str(law.get("official_url") or "").startswith(("https://", "http://"))
            and law.get("effectiveness_status") == "effective"
        ]

    # Adjacent chunks from one law/article often repeat the same legal rule.
    # Keep the strongest hit so the prompt remains focused and smaller.
    unique_laws: dict[tuple[str, str, str], dict[str, Any]] = {}
    for law in laws:
        key = (
            str(law.get("source_sha256") or law.get("label") or ""),
            str(law.get("law_name") or ""),
            str(law.get("article_no") or ""),
        )
        previous = unique_laws.get(key)
        if previous is None or float(law.get("score") or 0) > float(previous.get("score") or 0):
            unique_laws[key] = law
    laws = list(unique_laws.values())

    if len(laws) <= 1 or not rerank_enabled:
        result = laws[:top_k]
        return _store_cached_laws(cache_key, result) if cache_allowed else result

    try:
        ranked = rerank_documents(query, [law["content"] for law in laws], top_k)
    except Exception:
        result = laws[:top_k]
        return _store_cached_laws(cache_key, result) if cache_allowed else result

    reranked = []
    for index, score in ranked:
        law = laws[index].copy()
        law["rerank_score"] = score
        reranked.append(law)
    result = reranked or laws[:top_k]
    return _store_cached_laws(cache_key, result) if cache_allowed else result
    return laws


def format_laws_for_prompt(laws: list[dict[str, Any]]) -> str:
    if not laws:
        return "未检索到可用法条。请仅基于合同文本给出审查意见，并说明缺少可引用依据。"

    lines = []
    for index, law in enumerate(laws, start=1):
        label = law.get("label") or _law_label(law)
        content = str(law.get("content", "")).strip()
        metadata = []
        if law.get("effectiveness_status"):
            metadata.append(f"效力状态={law['effectiveness_status']}")
        if law.get("effective_date"):
            metadata.append(f"施行日期={law['effective_date']}")
        if law.get("official_url"):
            metadata.append(f"官方来源={law['official_url']}")
        suffix = f"（{'；'.join(metadata)}）" if metadata else ""
        lines.append(f"{index}. {label}{suffix}：{content}")
    return "\n".join(lines)
