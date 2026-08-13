from app.schemas.review import DeepReviewOutput, DocumentPreflightCheck, ReviewCoverage, ReviewResponse
from app.services.review_report import render_review_report


def test_report_renders_coverage_as_topic_and_status() -> None:
    report = render_review_report(
        ReviewResponse(
            filename="contract.docx",
            risks=[],
            coverage=[ReviewCoverage(topic="付款与发票", status="checked")],
        )
    )
    assert "付款与发票（checked）" in report
    assert "topic=&quot;" not in report


def test_report_renders_preflight_checks_separately_from_legal_risks() -> None:
    report = render_review_report(
        ReviewResponse(
            filename="contract.docx",
            risks=[],
            preflight_checks=[
                DocumentPreflightCheck(
                    category="punctuation",
                    title="重复中文标点",
                    status="warning",
                    evidence="服务范围，，验收",
                    suggestion="删除重复标点。",
                )
            ],
        )
    )

    assert "基础质量与合同框架预检" in report
    assert "重复中文标点" in report
    assert "服务范围，，验收" in report
