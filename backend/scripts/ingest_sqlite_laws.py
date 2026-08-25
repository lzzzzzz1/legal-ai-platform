"""Re-embed a legal SQLite knowledge base into a compatible Qdrant collection.

The source package contains 384-dimensional local TF-IDF vectors. They cannot
be mixed with this application's 1024-dimensional DashScope embeddings, so
this importer reads the original legal text and embeds it again.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


load_dotenv()
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

EMBEDDING_DIMENSION = 1024
DEFAULT_COLLECTION = "legal_laws_v2_1024"
DEFAULT_MAX_CHARS = 6000
DEFAULT_BATCH_SIZE = 32


def _ensure_no_proxy() -> None:
    additions = ["qdrant", "localhost", "127.0.0.1"]
    for key in ("NO_PROXY", "no_proxy"):
        existing = [item.strip() for item in os.getenv(key, "").split(",") if item.strip()]
        os.environ[key] = ",".join(existing + [item for item in additions if item not in existing])


def _split_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    units = [part.strip() for part in text.splitlines() if part.strip()]
    if len(units) <= 1:
        units = [part.strip() for part in text.replace("。", "。\n").replace("；", "；\n").splitlines() if part.strip()]

    segments: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.append(unit[:max_chars])
            unit = unit[max_chars:]
        candidate = unit if not current else f"{current}\n{unit}"
        if current and len(candidate) > max_chars:
            segments.append(current)
            current = unit
        else:
            current = candidate
    if current:
        segments.append(current)
    return segments or [text[:max_chars]]


def _connect_readonly(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def _load_records(database: Path, max_chars: int, limit: int | None) -> list[dict[str, Any]]:
    connection = _connect_readonly(database)
    query = """
        SELECT c.chunk_id, c.instrument_id, c.document_id, c.content, c.article_no,
               c.paragraph_no, c.item_no, c.page_start, c.page_end, c.heading,
               li.title, li.authority, li.document_number, li.jurisdiction,
               li.legal_level, li.published_date, li.effective_date,
               li.effectiveness_status, li.official_url, li.official_source_title,
               li.last_verified_at, d.source_path, d.filename, d.raw_sha256
        FROM legal_chunks c
        JOIN legal_instruments li ON li.instrument_id = c.instrument_id
        LEFT JOIN documents d ON d.document_id = c.document_id
        WHERE LENGTH(TRIM(c.content)) > 0
        ORDER BY c.chunk_id
    """
    rows = connection.execute(query).fetchall()
    connection.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        content = str(row["content"] or "").strip()
        heading = str(row["heading"] or "").strip()
        embedding_text = content
        if heading and not content.startswith(heading):
            embedding_text = f"{heading}\n{content}"
        segments = _split_text(embedding_text, max_chars)
        if limit is not None and len(records) >= limit:
            break
        for segment_index, segment in enumerate(segments):
            if limit is not None and len(records) >= limit:
                break
            records.append(
                {
                    "chunk_id": row["chunk_id"],
                    "instrument_id": row["instrument_id"],
                    "document_id": row["document_id"],
                    "content": segment,
                    "article_no": row["article_no"],
                    "paragraph_no": row["paragraph_no"],
                    "item_no": row["item_no"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "heading": row["heading"],
                    "law_name": row["title"],
                    "authority": row["authority"],
                    "document_number": row["document_number"],
                    "jurisdiction": row["jurisdiction"],
                    "legal_level": row["legal_level"],
                    "published_date": row["published_date"],
                    "effective_date": row["effective_date"],
                    "effectiveness_status": row["effectiveness_status"],
                    "official_url": row["official_url"],
                    "official_source_title": row["official_source_title"],
                    "last_verified_at": row["last_verified_at"],
                    "source_path": row["source_path"],
                    "source_filename": row["filename"],
                    "source_sha256": row["raw_sha256"],
                    "original_chunk_id": row["chunk_id"],
                    "segment_index": segment_index,
                    "segment_count": len(segments),
                }
            )
    return records


def _payload(record: dict[str, Any], model: str) -> dict[str, Any]:
    payload = {key: value for key, value in record.items() if value is not None}
    payload["embedding_model"] = model
    payload["embedding_dimensions"] = EMBEDDING_DIMENSION
    return payload


def _point_id(record: dict[str, Any]) -> str:
    stable_key = f"{record['chunk_id']}:{record['segment_index']}:{record['content']}"
    return str(uuid5(NAMESPACE_URL, stable_key))


def _embedding_client() -> OpenAI:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured.")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv(
            "BAILIAN_EMBEDDING_BASE_URL",
            os.getenv("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ),
        timeout=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120")),
    )


def _embed(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    response = client.embeddings.create(model=model, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts) or any(len(vector) != EMBEDDING_DIMENSION for vector in vectors):
        lengths = [len(vector) for vector in vectors]
        raise RuntimeError(f"Embedding dimension mismatch: expected {EMBEDDING_DIMENSION}, got {lengths}")
    return vectors


def _qdrant_client() -> QdrantClient:
    _ensure_no_proxy()
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )


def _ensure_collection(client: QdrantClient, collection: str) -> None:
    if client.collection_exists(collection):
        info = client.get_collection(collection)
        size = info.config.params.vectors.size
        distance = info.config.params.vectors.distance
        if size != EMBEDDING_DIMENSION or distance != Distance.COSINE:
            raise RuntimeError(
                f"Collection '{collection}' has incompatible config: size={size}, distance={distance}."
            )
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )


def ingest(database: Path, collection: str, batch_size: int, max_chars: int, limit: int | None) -> int:
    model = os.getenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    records = _load_records(database, max_chars=max_chars, limit=limit)
    if not records:
        raise RuntimeError("No non-empty legal chunks found in the SQLite database.")

    embedding_client = _embedding_client()
    qdrant_client = _qdrant_client()
    _ensure_collection(qdrant_client, collection)

    loaded = 0
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        vectors = _embed(embedding_client, [record["content"] for record in batch], model)
        points = [
            PointStruct(
                id=_point_id(record),
                vector=vector,
                payload=_payload(record, model),
            )
            for record, vector in zip(batch, vectors)
        ]
        qdrant_client.upsert(collection_name=collection, points=points, wait=True)
        loaded += len(points)
        print(f"Loaded {loaded}/{len(records)} points", flush=True)
    return loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-embed a legal SQLite database into Qdrant.")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-embedding-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--limit", type=int, help="Import only the first N segments for a smoke test.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0 or args.max_embedding_chars <= 0:
        raise SystemExit("batch size and max embedding chars must be positive")
    count = ingest(
        database=args.database,
        collection=args.collection,
        batch_size=args.batch_size,
        max_chars=args.max_embedding_chars,
        limit=args.limit,
    )
    print(f"Imported {count} legal points into '{args.collection}'.")


if __name__ == "__main__":
    main()
