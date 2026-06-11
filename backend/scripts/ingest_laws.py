import argparse
import os
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

ARTICLE_PATTERN = re.compile(r"(第[零一二三四五六七八九十百千万\d]+条)")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "legal_laws")
DEFAULT_SOURCE_FILE = Path(__file__).resolve().parents[1] / "tests" / "data" / "civil_code_sample.txt"
EMBEDDING_DIMENSION = 1024


def ensure_qdrant_no_proxy() -> None:
    additions = ["qdrant", "localhost", "127.0.0.1"]
    for key in ("NO_PROXY", "no_proxy"):
        existing = [item.strip() for item in os.getenv(key, "").split(",") if item.strip()]
        merged = existing + [item for item in additions if item not in existing]
        os.environ[key] = ",".join(merged)


def split_law_articles(text: str) -> list[dict[str, str]]:
    matches = list(ARTICLE_PATTERN.finditer(text))
    if not matches:
        content = text.strip()
        return [{"article_no": "全文", "content": content}] if content else []

    articles = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            articles.append({"article_no": match.group(1), "content": content})
    return articles


def build_points(law_name: str, articles: list[dict[str, str]], vectors: list[list[float]]) -> list[PointStruct]:
    if len(articles) != len(vectors):
        raise ValueError("articles and vectors must have the same length")

    points = []
    for article, vector in zip(articles, vectors):
        point_id = str(uuid5(NAMESPACE_URL, f"{law_name}:{article['article_no']}:{article['content']}"))
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "law_name": law_name,
                    "article_no": article["article_no"],
                    "content": article["content"],
                },
            )
        )
    return points


def create_embedding_client() -> OpenAI:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured.")

    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120")),
    )


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    model = os.getenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    response = client.embeddings.create(model=model, input=texts, dimensions=EMBEDDING_DIMENSION)
    return [item.embedding for item in response.data]


def ensure_collection(client: QdrantClient, collection_name: str = COLLECTION_NAME) -> None:
    existing = {collection.name for collection in client.get_collections().collections}
    if collection_name in existing:
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )


def ingest_laws(source_file: Path, law_name: str, collection_name: str = COLLECTION_NAME) -> int:
    source_text = source_file.read_text(encoding="utf-8")
    articles = split_law_articles(source_text)
    if not articles:
        raise ValueError(f"No law articles found in {source_file}")

    embedding_client = create_embedding_client()
    vectors = embed_texts(embedding_client, [article["content"] for article in articles])
    points = build_points(law_name=law_name, articles=articles, vectors=vectors)

    ensure_qdrant_no_proxy()
    qdrant_client = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
    ensure_collection(qdrant_client, collection_name=collection_name)
    qdrant_client.upsert(collection_name=collection_name, points=points)
    return len(points)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest legal articles into local Qdrant.")
    parser.add_argument("--source-file", type=Path, default=DEFAULT_SOURCE_FILE)
    parser.add_argument("--law-name", default="中华人民共和国民法典")
    parser.add_argument("--collection", default=COLLECTION_NAME)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    count = ingest_laws(
        source_file=args.source_file,
        law_name=args.law_name,
        collection_name=args.collection,
    )
    print(f"Ingested {count} law articles into collection '{args.collection}'.")


if __name__ == "__main__":
    main()
