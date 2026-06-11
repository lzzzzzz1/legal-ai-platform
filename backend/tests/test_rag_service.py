from types import SimpleNamespace

from app.services.rag_service import format_laws_for_prompt, retrieve_relevant_laws


class FakeEmbeddingClient:
    class Embeddings:
        def create(self, model: str, input: list[str], dimensions: int):
            assert model == "text-embedding-v3"
            assert input == ["合同履行地点不明确"]
            assert dimensions == 1024
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.2] * dimensions)])

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
                },
            )
        ]


def test_retrieve_relevant_laws_returns_payload(monkeypatch) -> None:
    monkeypatch.setenv("BAILIAN_EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setenv("QDRANT_COLLECTION", "legal_laws")

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
