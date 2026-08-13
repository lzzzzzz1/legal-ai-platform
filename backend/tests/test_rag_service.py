from types import SimpleNamespace

from app.services.rag_service import format_laws_for_prompt, retrieve_relevant_laws


class FakeEmbeddingClient:
    class Embeddings:
        def create(self, model: str, input: list[str]):
            assert model == "text-embedding-v3"
            assert input == ["合同履行地点不明确"]
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.2] * 1024)])

    embeddings = Embeddings()


class FakeQdrantClient:
    def search(self, collection_name: str, query_vector: list[float], limit: int, with_payload: bool):
        assert collection_name == "legal_laws"
        assert len(query_vector) == 1024
        assert limit == 2
        assert with_payload is True
        return [
            SimpleNamespace(
                score=0.91,
                payload={
                    "law_name": "中华人民共和国民法典",
                    "article_no": "第五百一十一条",
                    "content": "履行地点不明确的规则。",
                    "effectiveness_status": "effective",
                    "official_url": "https://official.example/law",
                },
            )
        ]


def test_retrieve_relevant_laws_returns_payload(monkeypatch) -> None:
    monkeypatch.setenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setenv("QDRANT_COLLECTION", "legal_laws")
    monkeypatch.setenv("RERANK_ENABLED", "false")
    monkeypatch.setenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")

    laws = retrieve_relevant_laws(
        "合同履行地点不明确",
        top_k=2,
        embedding_client=FakeEmbeddingClient(),
        qdrant_client=FakeQdrantClient(),
    )

    assert laws == [
        {
            "law_name": "中华人民共和国民法典",
            "article_no": "第五百一十一条",
            "content": "履行地点不明确的规则。",
            "score": 0.91,
            "label": "《中华人民共和国民法典》第五百一十一条",
            "effectiveness_status": "effective",
            "official_url": "https://official.example/law",
        }
    ]


def test_format_laws_for_prompt_includes_label_and_content() -> None:
    prompt = format_laws_for_prompt(
        [
            {
                "label": "《中华人民共和国民法典》第五百一十一条",
                "content": "履行地点不明确的规则。",
            }
        ]
    )

    assert "《中华人民共和国民法典》第五百一十一条" in prompt
    assert "履行地点不明确的规则。" in prompt
def test_review_topics_are_added_to_retrieval_query(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class TopicEmbedding:
        class Embeddings:
            def create(self, model: str, input: list[str]):
                captured["query"] = input[0]
                return SimpleNamespace(data=[SimpleNamespace(embedding=[0.2] * 1024)])

        embeddings = Embeddings()

    class TopicQdrant:
        def search(self, **kwargs):
            return []

    monkeypatch.setenv("RERANK_ENABLED", "false")
    monkeypatch.setenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    retrieve_relevant_laws(
        "合同文本",
        review_topics=["付款与发票"],
        embedding_client=TopicEmbedding(),
        qdrant_client=TopicQdrant(),
    )
    assert "付款与发票" in captured["query"]


def test_retrieval_requires_effective_official_source(monkeypatch) -> None:
    class StrictQdrant:
        def search(self, **kwargs):
            return [
                SimpleNamespace(score=0.9, payload={
                    "law_name": "未核验法规", "article_no": "第1条", "content": "内容",
                    "effectiveness_status": "effective", "official_url": "",
                }),
                SimpleNamespace(score=0.8, payload={
                    "law_name": "官方有效法规", "article_no": "第2条", "content": "内容",
                    "effectiveness_status": "effective", "official_url": "https://official.example/law",
                }),
            ]

    monkeypatch.setenv("RERANK_ENABLED", "false")
    monkeypatch.setenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    laws = retrieve_relevant_laws(
        "合同履行地点不明确",
        embedding_client=FakeEmbeddingClient(),
        qdrant_client=StrictQdrant(),
    )
    assert len(laws) == 1
    assert laws[0]["law_name"] == "官方有效法规"
