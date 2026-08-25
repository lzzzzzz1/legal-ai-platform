from app.services.document_preflight import PREFLIGHT_SCOPE, run_document_preflight
from app.services.openai_review import review_contract_text


def test_preflight_checks_contract_frame_and_objective_text_quality() -> None:
    checks = run_document_preflight(
        "采购合同\n甲方：甲公司；乙方：乙公司。\n"
        "第一条 服务范围：系统部署，，验收收。\n"
        "甲方授权代表签署并盖章。"
    )

    by_title = {check.title: check for check in checks if check.status == "passed"}
    warnings = [check for check in checks if check.status == "warning"]

    assert by_title["合同标题"].status == "passed"
    assert by_title["合同主体标识"].status == "passed"
    assert by_title["正文条款层级"].status == "passed"
    assert any(check.title == "重复中文标点" for check in warnings)
    punctuation = next(check for check in warnings if check.title == "重复中文标点")
    assert punctuation.auto_fixable is True
    assert punctuation.original_text == "，，"
    assert punctuation.replacement_text == "，"


def test_preflight_only_review_skips_model_and_does_not_create_legal_risks(monkeypatch) -> None:
    def should_not_call_model(*_args, **_kwargs):
        raise AssertionError("preflight-only review must not call the model")

    monkeypatch.setattr("app.services.openai_review._review_contract_segment", should_not_call_model)
    review = review_contract_text(
        "合同\n甲方与乙方签署。\n第一条 服务范围，，\n签字盖章。",
        "contract.docx",
        [PREFLIGHT_SCOPE],
    )

    assert review.risks == []
    assert review.review_scope == [PREFLIGHT_SCOPE]
    assert review.coverage[0].topic == PREFLIGHT_SCOPE
    assert review.coverage[0].status == "checked"
    assert review.preflight_checks
    assert review.review_status == "partial"
    assert review.manual_review_required is True
