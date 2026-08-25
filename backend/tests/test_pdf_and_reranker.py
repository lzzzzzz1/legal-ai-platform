import httpx

from app.services.pdf_parser import _quality_status, extract_pdf_text
from app.services.reranker import rerank_documents


def test_pdf_parser_uses_markdown(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"pages": 2, "markdown": "# 合同\n正文"})

    monkeypatch.setattr("app.services.pdf_parser.httpx.post", fake_post)
    assert extract_pdf_text(b"pdf", "contract.pdf") == "# 合同\n正文"


def test_pdf_parser_preserves_page_markers(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(200, json={"pages": [{"text": "第一页"}, {"markdown": "第二页"}]})

    monkeypatch.setattr("app.services.pdf_parser.httpx.post", fake_post)
    text = extract_pdf_text(b"pdf", "scan.pdf")
    assert "[PDF第1页]" in text
    assert "[PDF第2页]" in text


def test_pdf_quality_flags_low_text_as_partial() -> None:
    assert _quality_status(40, 2, "[PDF第1页]\n正文\n\n[PDF第2页]") == "partial"


def test_pdf_quality_accepts_sufficient_searchable_text() -> None:
    text = "[PDF第1页]\n" + ("合同条款 " * 30)
    assert _quality_status(len("".join(text.split())), 1, text) == "searchable"


def test_reranker_parses_vllm_results(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]},
        )

    monkeypatch.setattr("app.services.reranker.httpx.post", fake_post)
    assert rerank_documents("query", ["a", "b"], 2) == [(1, 0.9), (0, 0.2)]
