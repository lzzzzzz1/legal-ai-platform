"""Validated runtime settings shared by the API and background workers.

This module intentionally reads environment variables on demand.  It keeps
tests and local development reloads predictable while ensuring malformed
optional configuration never prevents the HTTP service from starting.
"""

from __future__ import annotations

import os


DEFAULT_REVIEW_JOB_DB = "data/review_jobs.sqlite3"


def non_negative_int_env(name: str, default: int) -> int:
    """Read an optional non-negative integer without making startup brittle."""
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def positive_float_env(name: str, default: float, minimum: float) -> float:
    """Read an optional positive float and recover safely from bad input."""
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def review_job_runtime_config() -> dict[str, object]:
    """Return the minimal validated configuration for resumable review jobs."""
    return {
        "path": os.getenv("REVIEW_JOB_DB", DEFAULT_REVIEW_JOB_DB),
        "retention_days": non_negative_int_env("REVIEW_JOB_RETENTION_DAYS", 7),
        "poll_seconds": positive_float_env("REVIEW_JOB_POLL_SECONDS", 1.0, 0.1),
    }
