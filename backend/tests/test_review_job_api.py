from fastapi.testclient import TestClient

from app.main import app
from app.services.review_jobs import ReviewJobStore, ReviewJobWorker


def _payload():
    return {
        "filename": "contract.docx",
        "contract_text": "甲方应按期付款。",
        "settings": {"party_role": "party_a"},
    }


def test_create_review_job_returns_202_and_tenant_filters_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("REVIEW_JOB_WORKER_ENABLED", "false")

    with TestClient(app) as client:
        response = client.post(
            "/api/review-jobs",
            headers={"X-Tenant-ID": "acme"},
            json=_payload(),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["job_type"] == "deep_review"
        assert "request" not in body

        same_tenant = client.get(
            f"/api/review-jobs/{body['job_id']}",
            headers={"X-Tenant-ID": "acme"},
        )
        assert same_tenant.status_code == 200
        assert same_tenant.json()["status"] == "queued"

        other_tenant = client.get(
            f"/api/review-jobs/{body['job_id']}",
            headers={"X-Tenant-ID": "other"},
        )
        assert other_tenant.status_code == 404


def test_review_job_config_recovers_from_invalid_environment_values(monkeypatch):
    from app.main import job_runtime_config

    monkeypatch.setenv("REVIEW_JOB_RETENTION_DAYS", "not-a-number")
    monkeypatch.setenv("REVIEW_JOB_POLL_SECONDS", "not-a-number")

    assert job_runtime_config()["retention_days"] == 7
    assert job_runtime_config()["poll_seconds"] == 1.0


def test_worker_persists_successful_result(tmp_path):
    store = ReviewJobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(tenant_id="local", job_type="deep_review", request={"value": 7})
    worker = ReviewJobWorker(store, lambda request: {"value": request["value"] * 2})

    assert worker.run_once() is True

    result = store.get_job(job.job_id, "local")
    assert result.status == "succeeded"
    assert result.result == {"value": 14}


def test_worker_persists_safe_failure(tmp_path):
    store = ReviewJobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(tenant_id="local", job_type="deep_review", request={})
    worker = ReviewJobWorker(store, lambda _request: (_ for _ in ()).throw(RuntimeError("secret")))

    assert worker.run_once() is True

    result = store.get_job(job.job_id, "local")
    assert result.status == "failed"
    assert result.error == "审查任务执行失败，请检查模型服务后重试。"
