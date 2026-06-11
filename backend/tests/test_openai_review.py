import pytest
from pydantic import ValidationError

from app.services.openai_review import parse_review_response


def test_parse_review_response_accepts_risks_object() -> None:
    response = parse_review_response(
        content=(
            '{"risks":[{"item":"税务条款","level":"high",'
            '"risk":"缺少税费承担约定","suggestion":"补充税费承担主体。"}]}'
        ),
        filename="contract.docx",
    )

    assert response.filename == "contract.docx"
    assert response.risks[0].item == "税务条款"


def test_parse_review_response_accepts_top_level_array() -> None:
    response = parse_review_response(
        content=(
            '[{"item":"合同份数","level":"low",'
            '"risk":"份数约定不清","suggestion":"明确一式几份。"}]'
        ),
        filename="contract.docx",
    )

    assert response.risks[0].level == "low"


def test_parse_review_response_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError):
        parse_review_response(
            content=(
                '{"risks":[{"item":"联系人信息","level":"critical",'
                '"risk":"缺少联系人","suggestion":"补充联系人。"}]}'
            ),
            filename="contract.docx",
        )
