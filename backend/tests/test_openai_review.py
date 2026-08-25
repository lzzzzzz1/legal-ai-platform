from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.review import ReviewCoverage, ReviewResponse, ReviewRisk
from app.services import openai_review
from app.services.openai_review import parse_review_response, review_contract_text
from app.services.consistency_review import run_consistency_checks


def test_consistency_checks_are_conservative_and_explainable() -> None:
    checks = run_consistency_checks(
        "甲方与乙方确认总价为人民币100元，另有附件。",
        ["主体与签约权限", "标的与价格", "附件与文本一致性"],
    )

    by_name = {check.check: check for check in checks}
    assert by_name["合同主体角色完整性"].status == "checked"
    assert by_name["金额与总价一致性"].status == "checked"
    assert by_name["附件引用一致性"].status == "warning"
    assert by_name["附件引用一致性"].note


def test_consistency_checks_flag_missing_operational_details() -> None:
    checks = run_consistency_checks(
        "甲方与乙方约定付款。合同自双方签署后生效。通知应以书面方式发出。争议由双方协商解决。",
        ["付款与发票", "合同成立与效力", "通知与送达", "争议解决"],
    )

    by_name = {check.check: check for check in checks}
    assert by_name["付款期限可执行性"].status == "warning"
    assert by_name["生效日期可识别性"].status == "warning"
    assert by_name["通知信息可送达性"].status == "warning"
    assert by_name["争议解决路径明确性"].status == "warning"


def test_consistency_checks_accept_operational_details() -> None:
    checks = run_consistency_checks(
        "合同于2026-08-12签订并生效。甲方应在收到发票后10个工作日付款。"
        "通知送达邮箱为 legal@example.com。因本合同产生的争议提交上海仲裁委员会仲裁。",
        ["付款与发票", "合同成立与效力", "通知与送达", "争议解决"],
    )

    by_name = {check.check: check for check in checks}
    assert by_name["付款期限可执行性"].status == "checked"
    assert by_name["生效日期可识别性"].status == "checked"
    assert by_name["通知信息可送达性"].status == "checked"
    assert by_name["争议解决路径明确性"].status == "checked"


def test_parse_review_response_extracts_json_from_model_prose() -> None:
    response = parse_review_response(
        '我将以 JSON 返回结果：\n```json\n{"risks":[]}\n```',
        filename="contract.docx",
    )
    assert response.risks == []


def test_json_parser_supports_openai_compatible_text_blocks() -> None:
    payload = openai_review._parse_json_content([
        {"type": "text", "text": '{"review_summary":"已完成审查",'},
        {"type": "text", "text": '"risks":[]}'},
    ])

    assert payload == {"review_summary": "已完成审查", "risks": []}


def test_finalize_marks_unlocatable_model_quote_for_manual_review() -> None:
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="payment",
                level="high",
                original_text="text that is not in contract",
                risk="risk",
                suggestion="suggestion",
            )
        ],
        review_summary="summary",
    )

    finalized = openai_review._finalize_review(review, "actual contract text", 10)

    assert finalized.review_status == "partial"
    assert any("原文定位" in warning for warning in finalized.warnings)


def test_finalize_surfaces_model_rule_coverage_conflict() -> None:
    topic = openai_review.RULE_TOPICS[0].name
    review = ReviewResponse(
        filename="contract.docx",
        risks=[],
        review_summary="summary",
        coverage=[ReviewCoverage(topic=topic, status="checked", evidence="model evidence")],
    )

    finalized = openai_review._finalize_review(review, "unrelated text", 10)

    assert finalized.review_status == "partial"
    assert any("模型审查与规则检查存在冲突" in warning for warning in finalized.warnings)


def test_finalize_keeps_preflight_warning_and_requires_review_for_empty_substantive_result(monkeypatch) -> None:
    monkeypatch.setattr(
        openai_review,
        "run_rule_fallback",
        lambda _text, scope: (
            [],
            [ReviewCoverage(topic=topic, status="checked", evidence="规则已检查") for topic in scope],
        ),
    )
    review = ReviewResponse(
        filename="contract.docx",
        risks=[],
        review_summary="已完成本次审查。",
        coverage=[
            ReviewCoverage(topic=topic.name, status="checked", evidence="规则已检查")
            for topic in openai_review.RULE_TOPICS
        ],
    )

    finalized = openai_review._finalize_review(
        review,
        "甲方与乙方约定合作；合同标题。",
        10,
    )

    assert any("基础质量预检发现" in warning for warning in finalized.warnings)
    assert any("不等同于合同无风险" in warning for warning in finalized.warnings)
    assert finalized.review_status == "needs_manual_review"
    assert finalized.manual_review_required is True


def test_unmatched_law_citations_are_removed_from_usable_result() -> None:
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="payment",
                level="high",
                original_text="actual clause",
                risk="risk",
                suggestion="suggestion",
                laws=["《不存在的法规》第1条", "《已核验法规》第1条"],
            )
        ],
    )

    checked = openai_review._validate_law_evidence(
        review,
        [{"label": "《已核验法规》第1条", "official_url": "https://official.example/law"}],
    )

    assert checked.risks[0].laws == ["《已核验法规》第1条"]
    assert checked.risks[0].evidence_status == "needs_manual_review"
    assert checked.risks[0].law_references[0].official_url == "https://official.example/law"


def test_audit_redaction_masks_common_identifiers() -> None:
    redacted = openai_review._redact_audit_text("a@b.com 13800138000 110101199001011234")
    assert "a@b.com" not in redacted
    assert "13800138000" not in redacted
    assert "110101199001011234" not in redacted


def test_paragraph_reference_recovers_an_exact_source_anchor() -> None:
    contract_text = "第一条 付款安排。\n甲方应在验收后付款。"
    indexed_text, references = openai_review.format_contract_with_paragraph_references(contract_text)
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="付款",
                level="medium",
                original_text="甲方应在验收后付款。",
                clause_reference="P002",
                risk="付款条件需核对。",
                suggestion="甲方应在验收合格并收到发票后付款。",
            )
        ],
    )

    hydrated = openai_review.hydrate_review_clause_references(review, references)

    assert "[P001]" in indexed_text
    assert hydrated.risks[0].anchor_text == "甲方应在验收后付款。"
    assert any("段落编号" in warning for warning in hydrated.warnings)


def test_quote_repair_only_accepts_verbatim_text_from_the_source_paragraph(monkeypatch) -> None:
    contract_text = "乙方仅赔偿直接损失，不承担间接损失。"
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="责任限制",
                level="high",
                original_text="乙方不承担全部损失",
                anchor_text=contract_text,
                risk="责任限制过宽。",
                suggestion="乙方应赔偿全部损失。",
            )
        ],
    )

    class FakeCompletions:
        def create(self, **_: object):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"matches":[{"risk_index":0,"original_text":"乙方仅赔偿直接损失"}]}'))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    repaired = openai_review.repair_unlocatable_risk_quotes(fake_client, review, contract_text)

    assert repaired.risks[0].original_text == "乙方仅赔偿直接损失"
    assert any("逐字校验" in warning for warning in repaired.warnings)


def test_quote_repair_uses_verified_paragraph_when_short_quote_cannot_be_extracted() -> None:
    contract_text = "因非乙方原因造成的数据遗失、数据污染，乙方不承担任何责任。"
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="责任限制",
                level="high",
                original_text="乙方不承担全部损失",
                anchor_text=contract_text,
                clause_reference="P001",
                risk="责任限制过宽。",
                suggestion="乙方仅在存在过错时承担经证明的直接损失。",
            )
        ],
    )

    class FakeCompletions:
        def create(self, **_: object):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"matches":[]}'))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    repaired = openai_review.repair_unlocatable_risk_quotes(fake_client, review, contract_text)

    assert repaired.risks[0].original_text == contract_text
    assert any("整段修订" in warning for warning in repaired.warnings)


def test_unreferenced_risk_is_bound_by_a_unique_contract_quote() -> None:
    paragraph = "乙方应在验收合格后十个工作日内提供合法有效的增值税专用发票。"
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="发票",
                level="medium",
                original_text="验收合格后十个工作日内提供合法有效的增值税专用发票",
                risk="开票义务需要明确。",
                suggestion="乙方应在验收合格后十个工作日内提供合法有效的增值税专用发票。",
            )
        ],
    )

    bound = openai_review.bind_review_risks_to_unique_paragraphs(
        review,
        {"P001": "合同标题", "P002": paragraph},
    )

    assert bound.risks[0].clause_reference == "P002"
    assert bound.risks[0].anchor_text == paragraph
    assert any("唯一匹配" in warning for warning in bound.warnings)


def test_second_pass_recovers_paragraph_id_and_verbatim_quote() -> None:
    paragraph = "乙方仅赔偿直接损失，不承担间接损失。"
    review = ReviewResponse(
        filename="contract.docx",
        risks=[
            ReviewRisk(
                item="责任限制",
                level="high",
                original_text="乙方责任过轻",
                risk="责任限制过宽。",
                suggestion="乙方应赔偿经证明的直接损失。",
            )
        ],
    )

    class FakeCompletions:
        def create(self, **_: object):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"matches":[{"risk_index":0,"clause_reference":"P003","original_text":"乙方仅赔偿直接损失"}]}'))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    recovered = openai_review.recover_unreferenced_risk_locations(
        fake_client,
        review,
        {"P003": paragraph},
    )

    assert recovered.risks[0].clause_reference == "P003"
    assert recovered.risks[0].anchor_text == paragraph
    assert recovered.risks[0].original_text == "乙方仅赔偿直接损失"


def test_large_contract_is_split_and_merged(monkeypatch) -> None:
    monkeypatch.setattr(openai_review, "REVIEW_SEGMENT_CHARS", 1000)

    def fake_segment(text: str, filename: str) -> ReviewResponse:
        return ReviewResponse(
            filename=filename,
            contract_type="通用商务合同",
            risks=[
                ReviewRisk(
                    item="付款条款",
                    level="medium",
                    original_text="同一风险",
                    risk="风险",
                    suggestion="建议",
                    laws=["法条一"],
                )
            ],
        )

    monkeypatch.setattr(openai_review, "_review_contract_segment", fake_segment)
    contract_text = "第一段\n" + ("第二段内容\n" * 200) + "第三段"
    response = review_contract_text(contract_text, "large.docx")

    assert response.contract_text == contract_text
    assert len(response.risks) >= 1
    assert response.risks[0].laws == ["法条一"]
    assert {item.topic for item in response.coverage} == set(openai_review.REVIEW_SCOPE)


def test_segment_merge_deduplicates_same_contract_evidence(monkeypatch) -> None:
    monkeypatch.setattr(openai_review, "_split_contract_text", lambda _: ["segment one", "segment two"])
    calls = iter(["short suggestion", "a more complete suggestion"])

    def fake_segment(text: str, filename: str) -> ReviewResponse:
        return ReviewResponse(
            filename=filename,
            risks=[
                ReviewRisk(
                    item="payment",
                    level="medium",
                    original_text="same clause",
                    risk="same risk",
                    suggestion=next(calls),
                    laws=["law"],
                )
            ],
        )

    monkeypatch.setattr(openai_review, "_review_contract_segment", fake_segment)
    response = review_contract_text("contract text", "dedupe.docx")

    assert len([risk for risk in response.risks if risk.item == "payment"]) == 1
    assert response.risks[0].suggestion == "a more complete suggestion"


def test_empty_unexplained_model_response_retries_with_compact_schema(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = '{"risks":[]}' if len(calls) == 1 else (
                '{"contract_type":"通用商务合同","review_summary":"已复核所选范围，未形成可验证风险。","risks":[]}'
            )
            return type(
                "FakeResponse",
                (),
                {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
            )()

    class FakeOpenAI:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openai_review, "retrieve_relevant_laws", lambda _: [])

    response = review_contract_text("合同仅约定项目名称。", "contract.docx")

    assert len(calls) == 2
    assert response.review_summary.startswith("已复核")
    assert response.review_status == "partial"
    assert response.risks
    assert any(item.status == "missing" for item in response.coverage)


def test_malformed_model_and_repair_response_fall_back_to_rules(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    class FakeCompletions:
        def create(self, **kwargs):
            return type(
                "FakeResponse",
                (),
                {"choices": [type("Choice", (), {"message": type("Message", (), {"content": '{"risks":[{"检查项":"付款与发票","laws":[]}]}'})()})()]},
            )()

    class FakeOpenAI:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openai_review, "retrieve_relevant_laws", lambda _: [])

    response = review_contract_text("合同只写了项目名称。", "contract.docx")

    assert response.review_status == "partial"
    assert response.risks
    assert any("结构不完整" in warning for warning in response.warnings)


def test_no_json_response_retries_original_contract_with_compact_schema(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = "not json" if len(calls) == 1 else (
                '{"contract_type":"通用商务合同","review_summary":"已完成复核",'
                '"risks":[{"item":"付款与发票","level":"high",'
                '"original_text":"【缺失该约定】","risk":"缺少付款安排",'
                '"suggestion":"甲方应在验收后十日内付款。","laws":[]}]}'
            )
            return type(
                "FakeResponse", (),
                {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]},
            )()

    class FakeOpenAI:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openai_review, "retrieve_relevant_laws", lambda _: [])

    response = review_contract_text("甲方委托乙方提供服务。", "contract.docx", ["付款与发票"])

    assert len(calls) == 2
    assert response.review_summary == "已完成复核"
    assert response.risks[0].item == "付款与发票"
    assert "合同文本" in calls[1]["messages"][1]["content"]
    assert "参考法条" not in calls[1]["messages"][1]["content"]


def test_parse_review_response_accepts_risks_object() -> None:
    response = parse_review_response(
        content=(
            '{"contract_type":"采购合同","risks":[{"item":"税务条款","level":"high",'
            '"original_text":"税费承担未约定。",'
            '"risk":"缺少税费承担约定","suggestion":"补充税费承担主体。",'
            '"laws":["《中华人民共和国民法典》第四百七十条"]}]}'
        ),
        filename="contract.docx",
    )

    assert response.filename == "contract.docx"
    assert response.contract_type == "采购/供应合同"
    assert response.risks[0].item == "税务条款"
    assert response.risks[0].original_text == "税费承担未约定。"
    assert response.risks[0].laws == ["《中华人民共和国民法典》第四百七十条"]


def test_parse_review_response_accepts_top_level_array() -> None:
    response = parse_review_response(
        content=(
            '[{"item":"合同份数","level":"low",'
            '"original_text":"合同份数未约定。",'
            '"risk":"份数约定不清","suggestion":"明确一式几份。"}]'
        ),
        filename="contract.docx",
    )

    assert response.risks[0].level == "low"
    assert response.contract_type is None
    assert response.risks[0].laws == []


def test_parse_review_response_normalizes_laws_string() -> None:
    response = parse_review_response(
        content=(
            '{"risks":[{"item":"合同份数","level":"low",'
            '"original_text":"合同份数未约定。",'
            '"risk":"份数约定不清","suggestion":"明确一式几份。",'
            '"laws":"《中华人民共和国民法典》第四百七十条"}]}'
        ),
        filename="contract.docx",
    )

    assert response.risks[0].laws == ["《中华人民共和国民法典》第四百七十条"]


def test_parse_review_response_normalizes_unknown_contract_type_to_business_default() -> None:
    response = parse_review_response(
        content=(
            '{"contract_type":"框架合作协议","risks":[{"item":"通知条款","level":"medium",'
            '"original_text":"联系人未约定。",'
            '"risk":"缺少通知联系人","suggestion":"补充通知联系人与送达方式。"}]}'
        ),
        filename="contract.docx",
    )

    assert response.contract_type == "通用商务合同"


def test_parse_review_response_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        parse_review_response(
            content=(
                '{"risks":[{"item":"联系人信息","level":"critical",'
                '"original_text":"联系人未约定。",'
                '"risk":"缺少联系人","suggestion":"补充联系人。"}]}'
            ),
            filename="contract.docx",
        )


def test_review_contract_text_requires_dashscope_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        review_contract_text(contract_text="合同文本", filename="contract.docx")

    assert exc_info.value.status_code == 503
    assert "DASHSCOPE_API_KEY" in exc_info.value.detail


def test_review_contract_text_injects_retrieved_laws(monkeypatch) -> None:
    captured_messages = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured_messages["messages"] = kwargs["messages"]
            return type(
                "FakeResponse",
                (),
                {
                    "choices": [
                        type(
                            "FakeChoice",
                            (),
                            {
                                "message": type(
                                    "FakeMessage",
                                    (),
                                    {
                                        "content": (
                                            '{"contract_type":"服务合同","risks":[{"item":"签订地点","level":"medium",'
                                            '"original_text":"合同约定履行地点不明确。",'
                                            '"risk":"履行地点约定不明确",'
                                            '"suggestion":"根据《中华人民共和国民法典》第五百一十一条补充履行地点。",'
                                            '"laws":["《中华人民共和国民法典》第五百一十一条"]}]}'
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        chat = FakeChat()

        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(openai_review, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        openai_review,
        "retrieve_relevant_laws",
        lambda query_text: [
            {
                "label": "《中华人民共和国民法典》第五百一十一条",
                "content": "履行地点不明确的规则。",
            }
        ],
    )

    response = review_contract_text(contract_text="合同约定履行地点不明确。", filename="contract.docx")

    user_prompt = captured_messages["messages"][1]["content"]
    assert "参考法条" in user_prompt
    assert "contract_type" in user_prompt
    assert "采购/供应合同|销售/服务合同|保密协议|通用商务合同" in user_prompt
    assert "original_text" in user_prompt
    assert "《中华人民共和国民法典》第五百一十一条" in user_prompt
    assert response.contract_type == "销售/服务合同"
    assert response.risks[0].laws == ["《中华人民共和国民法典》第五百一十一条"]
