import os
from typing import Any

import httpx


def rerank_documents(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    if not query.strip() or not documents:
        return []

    url = os.getenv("RERANK_URL", "http://192.168.6.71:30002/v1/rerank")
    model = os.getenv("RERANK_MODEL", "Qwen3-Reranker-0.6B")
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    timeout = float(os.getenv("RERANK_TIMEOUT_SECONDS", "60"))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    max_document_chars = int(os.getenv("RERANK_DOCUMENT_MAX_CHARS", "1800"))
    rerank_documents = [document[:max_document_chars] for document in documents]
    payload = {"model": model, "query": query, "documents": rerank_documents, "top_n": top_n}

    response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Reranker returned HTTP {response.status_code}.")
    body: Any = response.json()
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        raise RuntimeError("Reranker returned no results array.")

    ranked: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, int) and 0 <= index < len(documents):
            ranked.append((index, float(score or 0.0)))
    return ranked[:top_n]
