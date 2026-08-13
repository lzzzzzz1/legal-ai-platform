"""Ingest locally saved laws only when their HTML declares an official source site.

This importer intentionally excludes PDFs and files without official-source metadata.
It appends to the configured 1024-dimensional Qdrant collection and is idempotent.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

EMBEDDING_DIMENSION = 1024
ARTICLE_PATTERN = re.compile(r"第[零一二三四五六七八九十百千万\d]+条")
OFFICIAL_DOMAINS = {
    "www.samr.gov.cn": "国家市场监督管理总局",
    "fgk.chinatax.gov.cn": "国家税务总局政策法规库",
    "www.cac.gov.cn": "中央网络安全和信息化委员会办公室",
}

RISK_TOPIC_KEYWORDS = {
    "付款与发票": ("民法典", "发票管理", "票据法", "中小企业款项支付"),
    "主体与签约权限": ("公司法", "市场主体登记", "外商投资法"),
    "合同成立与效力": ("民法典", "电子签名法"),
    "标的与价格": ("产品质量法", "电子商务法", "招标投标法"),
    "质量与售后": ("产品质量法", "药品管理法", "药品生产质量", "药品经营质量"),
    "违约与责任": ("民法典", "产品质量法", "反不正当竞争法"),
    "解除与终止": ("民法典", "劳动合同法"),
    "知识产权": ("专利法", "商标法", "著作权法", "电子商务法"),
    "保密与数据": ("网络安全法", "数据安全法", "个人信息保护法", "电子商务法"),
    "合规与许可": ("反洗钱法", "反垄断法", "药品管理法", "生产许可证"),
    "争议解决": ("民法典", "仲裁", "民事诉讼"),
    "附件与文本一致性": ("招标投标法", "电子商务法", "民法典"),
}


def _risk_topics(law_name: str) -> list[str]:
    return [topic for topic, keywords in RISK_TOPIC_KEYWORDS.items() if any(keyword in law_name for keyword in keywords)]

# Only values verified against an authoritative registry are populated here.
# Other official-source files remain searchable but are explicitly marked for review.
VERIFIED_VERSIONS: dict[str, dict[str, str]] = {
    "中华人民共和国民法典": {"legal_level": "law", "promulgated_date": "2020-05-28", "effective_date": "2021-01-01", "effectiveness_status": "effective"},
    "中华人民共和国公司法": {"legal_level": "law", "promulgated_date": "2023-12-29", "effective_date": "2024-07-01", "effectiveness_status": "effective"},
    "中华人民共和国个人信息保护法": {"legal_level": "law", "promulgated_date": "2021-08-20", "effective_date": "2021-11-01", "effectiveness_status": "effective"},
    "中华人民共和国网络安全法": {"legal_level": "law", "promulgated_date": "2025-10-28", "effective_date": "2026-01-01", "effectiveness_status": "effective"},
    "网络数据安全管理条例": {"legal_level": "administrative_regulation", "promulgated_date": "2024-09-24", "effective_date": "2025-01-01", "effectiveness_status": "effective"},
}

# Keep the canonical Chinese titles alongside legacy encoded entries above.
# The title is read from official HTML metadata, so exact matching matters.
VERIFIED_VERSIONS.update({
    "中华人民共和国民法典": {"legal_level": "law", "promulgated_date": "2020-05-28", "effective_date": "2021-01-01", "effectiveness_status": "effective"},
    "中华人民共和国公司法": {"legal_level": "law", "promulgated_date": "2023-12-29", "effective_date": "2024-07-01", "effectiveness_status": "effective"},
    "中华人民共和国个人信息保护法": {"legal_level": "law", "promulgated_date": "2021-08-20", "effective_date": "2021-11-01", "effectiveness_status": "effective"},
    "中华人民共和国网络安全法": {"legal_level": "law", "promulgated_date": "2016-11-07", "effective_date": "2017-06-01", "effectiveness_status": "effective"},
    "网络数据安全管理条例": {"legal_level": "administrative_regulation", "promulgated_date": "2024-09-24", "effective_date": "2025-01-01", "effectiveness_status": "effective"},
})


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        name = attrs_dict.get("name")
        content = attrs_dict.get("content")
        if name and content:
            self.meta[name] = html.unescape(content).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def _parse_html(path: Path) -> tuple[str, dict[str, str]]:
    parser = _TextParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    text = re.sub(r"\s+", " ", "\n".join(parser.parts)).strip()
    return text, parser.meta


def _split_articles(text: str, max_chars: int) -> list[dict[str, str]]:
    matches = list(ARTICLE_PATTERN.finditer(text))
    if not matches:
        return [{"article_no": "全文", "content": text[:max_chars]}] if text else []

    records: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        if not content:
            continue
        article_no = match.group(0)
        for offset in range(0, len(content), max_chars):
            records.append({"article_no": article_no, "content": content[offset : offset + max_chars]})
    return records


def _official_records(source_dir: Path, max_chars: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(source_dir.glob("*.html")):
        text, meta = _parse_html(path)
        domain = meta.get("SiteDomain", "").replace("https://", "").replace("http://", "").rstrip("/")
        site_name = OFFICIAL_DOMAINS.get(domain)
        title = meta.get("ArticleTitle") or path.stem.split("__", 1)[0]
        if not site_name or not text or not ARTICLE_PATTERN.search(text):
            continue
        source_url = meta.get("Url", "")
        if source_url and source_url.startswith("/"):
            source_url = f"https://{domain}{source_url}"
        raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        version = VERIFIED_VERSIONS.get(title, {})
        version_payload = {
            "jurisdiction": "中国大陆",
            "legal_level": version.get("legal_level", "needs_verification"),
            "promulgated_date": version.get("promulgated_date", ""),
            "effective_date": version.get("effective_date", ""),
            "effectiveness_status": version.get("effectiveness_status", "needs_verification"),
            "last_verified_at": date.today().isoformat() if version else "",
            "metadata_version": "law-metadata-v1",
            "risk_topics": _risk_topics(title),
            "verification_policy": "official-source-html-only",
        }
        for article in _split_articles(text, max_chars):
            records.append(
                {
                    **article,
                    "law_name": title,
                    "authority": site_name,
                    "source_domain": domain,
                    "official_url": source_url,
                    "published_date": meta.get("PubDate", ""),
                    "source_filename": path.name,
                    "source_sha256": raw_sha256,
                    "source_page_published_at": meta.get("PubDate", ""),
                    **version_payload,
                }
            )
    return records


def _embed(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    response = client.embeddings.create(model=model, input=texts)
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts) or any(len(vector) != EMBEDDING_DIMENSION for vector in vectors):
        raise RuntimeError("Embedding dimension mismatch; refusing to write points.")
    return vectors


def _point_id(record: dict[str, object]) -> str:
    stable = f"official:{record['source_sha256']}:{record['article_no']}:{record['content']}"
    return str(uuid5(NAMESPACE_URL, stable))


def ingest(source_dir: Path, collection: str, max_chars: int, batch_size: int, dry_run: bool) -> int:
    records = _official_records(source_dir, max_chars)
    if dry_run:
        topic_counts: dict[str, int] = {}
        for record in records:
            for topic in record.get("risk_topics", []):
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        print(json.dumps({
            "files": len({r['source_filename'] for r in records}),
            "chunks": len(records),
            "risk_topic_chunks": topic_counts,
            "verified_effective_chunks": sum(
                1 for record in records if record.get("effectiveness_status") == "effective"
            ),
        }, ensure_ascii=False))
        return len(records)
    if not records:
        raise RuntimeError("No locally saved files with approved official source metadata were found.")

    model = os.getenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("BAILIAN_EMBEDDING_BASE_URL", os.getenv("BAILIAN_BASE_URL")),
        timeout=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "120")),
    )
    qdrant = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"), api_key=os.getenv("QDRANT_API_KEY"))
    if not qdrant.collection_exists(collection):
        qdrant.create_collection(collection_name=collection, vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE))
    info = qdrant.get_collection(collection)
    if info.config.params.vectors.size != EMBEDDING_DIMENSION:
        raise RuntimeError(f"Collection {collection} is not a 1024-dimension collection.")

    loaded = 0
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        vectors = _embed(client, [str(item["content"]) for item in batch], model)
        points = []
        for record, vector in zip(batch, vectors):
            payload = {key: value for key, value in record.items() if value not in (None, "")}
            payload.update({"embedding_model": model, "embedding_dimensions": EMBEDDING_DIMENSION, "source_type": "official_local_html"})
            points.append(PointStruct(id=_point_id(record), vector=vector, payload=payload))
        qdrant.upsert(collection_name=collection, points=points, wait=True)
        loaded += len(points)
        print(f"ingested={loaded}/{len(records)}")
    return loaded


def refresh_metadata(source_dir: Path, collection: str, max_chars: int, batch_size: int) -> int:
    """Refresh version metadata on already-imported official points without re-embedding."""
    records = _official_records(source_dir, max_chars)
    qdrant = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"), api_key=os.getenv("QDRANT_API_KEY"))
    updated = 0
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        points = [_point_id(record) for record in batch]
        payload = {
            key: batch[0][key]
            for key in (
                "jurisdiction",
                "legal_level",
                "promulgated_date",
                "effective_date",
                "effectiveness_status",
                "last_verified_at",
                "metadata_version",
                "official_url",
                "authority",
                "source_domain",
                "source_filename",
                "source_sha256",
                "risk_topics",
                "verification_policy",
            )
            if key in batch[0]
        }
        # Metadata can differ by law within the batch, so update each point individually.
        for record, point_id in zip(batch, points):
            qdrant.set_payload(
                collection_name=collection,
                payload={key: record[key] for key in payload if key in record and record[key] not in (None, "")},
                points=[point_id],
                wait=False,
            )
            updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", "legal_laws_v2_1024"))
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-metadata", action="store_true")
    args = parser.parse_args()
    if args.refresh_metadata:
        print(refresh_metadata(args.source_dir, args.collection, args.max_chars, args.batch_size))
    else:
        print(ingest(args.source_dir, args.collection, args.max_chars, args.batch_size, args.dry_run))


if __name__ == "__main__":
    main()
