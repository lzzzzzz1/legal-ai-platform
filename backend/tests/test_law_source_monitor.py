from datetime import date, timedelta

import httpx

from app.services.law_source_monitor import check_sources, is_approved_official_url, summarize_source_health, verification_is_due


def test_official_url_requires_https_and_approved_domain() -> None:
    approved = {"www.samr.gov.cn"}
    assert is_approved_official_url("https://www.samr.gov.cn/law", approved)
    assert not is_approved_official_url("http://www.samr.gov.cn/law", approved)
    assert not is_approved_official_url("https://example.com/law", approved)


def test_verification_due_handles_missing_and_recent_dates() -> None:
    assert verification_is_due(None)
    assert not verification_is_due(date.today().isoformat())
    assert verification_is_due((date.today() - timedelta(days=91)).isoformat())


def test_source_check_records_health_without_claiming_effectiveness() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={"etag": "v1"}))
    with httpx.Client(transport=transport) as client:
        results = check_sources(
            [{"law_name": "测试法规", "official_url": "https://www.samr.gov.cn/law", "last_verified_at": "2020-01-01"}],
            {"www.samr.gov.cn"},
            client=client,
        )
    assert results[0]["status"] == "reachable"
    assert results[0]["verification_due"] is True
    assert "effectiveness_status" not in results[0]
    assert summarize_source_health(results) == {
        "sources": 1, "reachable": 1, "unapproved": 0, "unreachable": 0, "verification_due": 1,
    }
