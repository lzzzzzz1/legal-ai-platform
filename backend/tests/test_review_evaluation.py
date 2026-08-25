from app.services.review_evaluation import EVALUATION_CASES, run_review_evaluation


def test_rule_review_regression_suite_passes() -> None:
    report = run_review_evaluation()
    assert report["total"] == len(EVALUATION_CASES)
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0


def test_regression_suite_covers_critical_contract_topics() -> None:
    topics = {topic for case in EVALUATION_CASES for topic in case.selected_topics}
    assert {"付款与发票", "交付与验收", "违约与责任", "知识产权", "保密与数据", "争议解决"} <= topics
