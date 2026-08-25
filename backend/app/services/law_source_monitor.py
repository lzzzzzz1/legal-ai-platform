"""Auditable health checks for official legal-source URLs.

The checker never changes a law's effectiveness automatically. A reachable URL
only proves that the source page can be contacted; a legal professional must
still confirm a changed source before its metadata is promoted.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx


def is_approved_official_url(url: str, approved_domains: set[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host in {domain.lower() for domain in approved_domains}


def verification_is_due(last_verified_at: str | None, max_age_days: int = 90) -> bool:
    if not last_verified_at:
        return True
    try:
        verified = datetime.fromisoformat(last_verified_at[:10]).date()
    except ValueError:
        return True
    return (date.today() - verified).days > max_age_days


def check_sources(
    records: list[dict[str, Any]],
    approved_domains: set[str],
    timeout: float = 15,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Check connectivity and cache validators for unique official sources."""
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True, timeout=timeout)
    try:
        for record in records:
            law_name = str(record.get("law_name") or "")
            url = str(record.get("official_url") or "")
            key = (law_name, url)
            if not url or key in seen:
                continue
            seen.add(key)
            result: dict[str, Any] = {
                "law_name": law_name,
                "official_url": url,
                "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "approved_official_source": is_approved_official_url(url, approved_domains),
                "verification_due": verification_is_due(record.get("last_verified_at")),
            }
            if not result["approved_official_source"]:
                result.update({"reachable": False, "status": "unapproved_source"})
                results.append(result)
                continue
            try:
                response = active_client.head(url)
                if response.status_code in {403, 405}:
                    response = active_client.get(url, headers={"Range": "bytes=0-1023"})
                result.update({
                    "reachable": response.is_success,
                    "status": "reachable" if response.is_success else "http_error",
                    "status_code": response.status_code,
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                })
            except httpx.HTTPError as exc:
                result.update({"reachable": False, "status": "connection_error", "error": str(exc)})
            results.append(result)
    finally:
        if owns_client:
            active_client.close()
    return results


def summarize_source_health(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "sources": len(results),
        "reachable": sum(item.get("status") == "reachable" for item in results),
        "unapproved": sum(item.get("status") == "unapproved_source" for item in results),
        "unreachable": sum(not item.get("reachable") and item.get("status") != "unapproved_source" for item in results),
        "verification_due": sum(bool(item.get("verification_due")) for item in results),
    }
