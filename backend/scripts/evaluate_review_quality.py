"""Run the deterministic contract-review regression suite.

Exit code is non-zero if any expected rule-coverage outcome changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.review_evaluation import run_review_evaluation


def main() -> None:
    report = run_review_evaluation()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
