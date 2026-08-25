from pathlib import Path

from scripts import ingest_official_laws


def test_law_topic_mapping_is_conservative() -> None:
    assert "付款与发票" in ingest_official_laws._risk_topics("中华人民共和国民法典")
    assert ingest_official_laws._risk_topics("无关文件") == []


def test_official_records_have_source_metadata() -> None:
    source_dir = Path(r"C:\Users\liji1\Desktop\法规原文库")
    records = ingest_official_laws._official_records(source_dir, 6000)
    assert records
    assert all(record["official_url"] or record["source_domain"] for record in records)
