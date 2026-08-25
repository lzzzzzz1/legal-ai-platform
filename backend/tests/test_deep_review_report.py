from app.schemas.review import DeepReviewOutput, ReviewResponse
from app.services.review_report import render_review_report


def test_report_includes_completed_deep_review_sections() -> None:
    report = render_review_report(
        ReviewResponse(
            filename="contract.docx",
            risks=[],
            deep_review=DeepReviewOutput(
                state="completed",
                overall_conclusion="有条件可签",
                executive_summary="付款条件调整后再签署。",
                key_facts=[{"item": "付款", "contract_term": "预付款", "conclusion": "不利于我方"}],
                missing_clauses=["验收标准"],
                negotiation_items=[
                    {
                        "topic": "付款",
                        "target": "验收后付款",
                        "minimum_acceptable": "验收挂钩",
                        "owner": "法务",
                    }
                ],
                clarification_questions=["是否允许预付款？"],
            ),
        )
    )

    assert "深度商业与谈判审查" in report
    assert "有条件可签" in report
    assert "验收后付款" in report
