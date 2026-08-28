"""Persistent review-job storage primitives.

The store deliberately uses only the Python standard library so the local
deployment stays a three-container Compose stack.  Worker lifecycle code is
added on top of this small, transaction-safe store.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)


JOB_STATUSES = {"queued", "running", "succeeded", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReviewJob:
    job_id: str
    tenant_id: str
    job_type: str
    status: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    attempt_count: int


class ReviewJobStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed')),
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_review_jobs_queue
                    ON review_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_review_jobs_tenant
                    ON review_jobs(tenant_id, created_at);
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ReviewJob | None:
        if row is None:
            return None
        return ReviewJob(
            job_id=row["job_id"],
            tenant_id=row["tenant_id"],
            job_type=row["job_type"],
            status=row["status"],
            request=json.loads(row["request_json"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error_message"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
            attempt_count=int(row["attempt_count"]),
        )

    def create_job(
        self,
        *,
        tenant_id: str,
        job_type: str,
        request: dict[str, Any],
    ) -> ReviewJob:
        now = _utc_now()
        job_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_jobs(
                    job_id, tenant_id, job_type, status, request_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, tenant_id, job_type, json.dumps(request, ensure_ascii=False), now, now),
            )
            row = connection.execute(
                "SELECT * FROM review_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._from_row(row)  # type: ignore[return-value]

    def get_job(self, job_id: str, tenant_id: str) -> ReviewJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_jobs WHERE job_id = ? AND tenant_id = ?",
                (job_id, tenant_id),
            ).fetchone()
        return self._from_row(row)

    def claim_next_job(self) -> ReviewJob | None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM review_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE review_jobs
                SET status = 'running', started_at = ?, updated_at = ?,
                    attempt_count = attempt_count + 1
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, now, row["job_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM review_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            connection.commit()
        return self._from_row(claimed)

    def complete_job(self, job_id: str, result: dict[str, Any]) -> None:
        self._finish_job(job_id, "succeeded", result=result)

    def fail_job(self, job_id: str, error_message: str) -> None:
        self._finish_job(job_id, "failed", error_message=error_message)

    def _finish_job(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"Unsupported terminal status: {status}")
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE review_jobs
                SET status = ?, result_json = ?, error_message = ?,
                    finished_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error_message,
                    now,
                    now,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Review job {job_id} is not running or does not exist")

    def recover_running_jobs(self) -> int:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE review_jobs
                SET status = 'queued', started_at = NULL, updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            return cursor.rowcount

    def cleanup_expired(self, retention_days: int) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM review_jobs
                WHERE status IN ('succeeded', 'failed') AND finished_at < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount



class ReviewJobWorker:
    """Small in-process worker used by the local deployment."""

    def __init__(self, store: ReviewJobStore, review_fn, poll_seconds: float = 1.0) -> None:
        self.store = store
        self.review_fn = review_fn
        self.poll_seconds = max(0.1, poll_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="review-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def run_once(self) -> bool:
        job = self.store.claim_next_job()
        if job is None:
            return False
        try:
            result = self.review_fn(job.request)
            self.store.complete_job(job.job_id, result)
        except Exception:
            logger.exception("Review job %s failed", job.job_id)
            self.store.fail_job(
                job.job_id,
                "审查任务执行失败，请检查模型服务后重试。",
            )
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self.run_once():
                self._stop_event.wait(self.poll_seconds)
