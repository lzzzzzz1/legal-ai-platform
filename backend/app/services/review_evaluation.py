"""Deterministic regression scenarios for the contract-review safety net.

These fixtures intentionally exercise the rule layer only. They are fast,
repeatable, and independent of a model provider so they can catch accidental
coverage regressions before a release.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.rule_review import run_rule_fallback


@dataclass(frozen=True)
class ReviewEvaluationCase:
    name: str
    contract_text: str
    selected_topics: tuple[str, ...]
    expected_missing: tuple[str, ...]


EVALUATION_CASES = (
    ReviewEvaluationCase(
        name="付款条款缺失",
        contract_text="甲方委托乙方提供软件实施服务，双方另行协商价格。",
        selected_topics=("付款与发票", "交付与验收"),
        expected_missing=("付款与发票", "交付与验收"),
    ),
    ReviewEvaluationCase(
        name="付款与验收已出现",
        contract_text="甲方应在验收合格后支付价款，乙方应于2026年9月1日前交付系统并接受验收。",
        selected_topics=("付款与发票", "交付与验收"),
        expected_missing=(),
    ),
    ReviewEvaluationCase(
        name="违约责任缺失",
        contract_text="双方应按照约定履行义务，服务期限为一年。",
        selected_topics=("违约与责任", "解除与终止"),
        expected_missing=("违约与责任", "解除与终止"),
    ),
    ReviewEvaluationCase(
        name="知识产权与数据缺失",
        contract_text="乙方为甲方提供系统开发及运维服务。",
        selected_topics=("知识产权", "保密与数据"),
        expected_missing=("知识产权", "保密与数据"),
    ),
    ReviewEvaluationCase(
        name="保密与争议解决已出现",
        contract_text="双方应对商业秘密承担保密义务。因本合同产生的争议提交上海仲裁委员会仲裁。",
        selected_topics=("保密与数据", "争议解决"),
        expected_missing=(),
    ),
    ReviewEvaluationCase(
        name="主体签约信息缺失",
        contract_text="本协议由双方于签署日订立并生效。",
        selected_topics=("主体与签约权限", "合同成立与效力"),
        expected_missing=("主体与签约权限",),
    ),
)


def run_review_evaluation() -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case in EVALUATION_CASES:
        risks, coverage = run_rule_fallback(case.contract_text, list(case.selected_topics))
        actual_missing = tuple(item.topic for item in coverage if item.status == "missing")
        expected = set(case.expected_missing)
        actual = set(actual_missing)
        results.append({
            "name": case.name,
            "passed": actual == expected,
            "expected_missing": list(case.expected_missing),
            "actual_missing": list(actual_missing),
            "risk_count": len(risks),
        })

    passed = sum(bool(result["passed"]) for result in results)
    return {
        "suite": "rule-review-regression-v1",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 1.0,
        "cases": results,
    }
