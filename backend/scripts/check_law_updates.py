"""Check approved official legal sources without changing law metadata.

The output is a JSON audit report. A source being reachable does not mean the
law remains effective; changed or overdue sources require human verification.
"""

import argparse
import json
from pathlib import Path

from app.services.law_source_monitor import check_sources, summarize_source_health
from scripts.ingest_official_laws import OFFICIAL_DOMAINS, _official_records


def _records_from_manifest(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("source manifest must contain a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source-manifest", type=Path, help="JSON list containing law_name and official_url")
    group.add_argument("--source-dir", type=Path, help="Directory of locally saved official HTML files")
    parser.add_argument("--max-age-days", type=int, default=90)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path")
    args = parser.parse_args()

    records = (
        _records_from_manifest(args.source_manifest)
        if args.source_manifest
        else _official_records(args.source_dir, max_chars=6000)
    )
    results = check_sources(records, set(OFFICIAL_DOMAINS), timeout=args.timeout)
    report = {"summary": summarize_source_health(results), "sources": results}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
