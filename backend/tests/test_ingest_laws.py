from qdrant_client.models import PointStruct

from scripts.ingest_laws import build_points, split_law_articles


def test_split_law_articles_keeps_article_content() -> None:
    text = "标题\n第四百六十四条 合同是协议。\n\n第四百六十五条 依法成立的合同受保护。"

    articles = split_law_articles(text)

    assert len(articles) == 2
    assert articles[0]["article_no"] == "第四百六十四条"
    assert articles[0]["content"].startswith("第四百六十四条")
    assert "第四百六十五条" not in articles[0]["content"]
    assert articles[1]["article_no"] == "第四百六十五条"


def test_build_points_adds_law_payload() -> None:
    articles = [{"article_no": "第四百七十条", "content": "第四百七十条 合同内容由当事人约定。"}]
    vectors = [[0.1] * 1024]

    points = build_points(law_name="中华人民共和国民法典", articles=articles, vectors=vectors)

    assert len(points) == 1
    assert isinstance(points[0], PointStruct)
    assert points[0].payload == {
        "law_name": "中华人民共和国民法典",
        "article_no": "第四百七十条",
        "content": "第四百七十条 合同内容由当事人约定。",
    }
