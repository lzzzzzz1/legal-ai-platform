from app.schemas.review import ReviewResponse


def verify_high_risk_findings(review: ReviewResponse) -> ReviewResponse:
    """Second-pass gate for high-risk findings before a review can be complete."""
    unverified = [
        risk.item
        for risk in review.risks
        if risk.level == "high" and risk.evidence_status != "verified"
    ]
    if unverified:
        review.warnings.append(
            "高风险项二次复核未通过：" + "、".join(dict.fromkeys(unverified))
        )
    return review
