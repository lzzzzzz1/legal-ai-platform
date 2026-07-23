from fastapi.testclient import TestClient

from app.main import app
from app.services.knowledge_import import (
    KnowledgeImportConflict,
    KnowledgeImportResult,
)


client = TestClient(app)


def _upload(snapshot_name: str = "playbook.snapshot", **kwargs):
    return client.post(
        "/api/admin/knowledge/snapshots",
        headers=kwargs.pop("headers", {"X-Admin-Token": "test-admin-token"}),
        data={
            "tenant_id": kwargs.pop("tenant_id", "acme"),
            "knowledge_type": kwargs.pop("knowledge_type", "enterprise_playbook"),
        },
        files={"file": (snapshot_name, b"snapshot-data", "application/octet-stream")},
    )


def test_import_snapshot_requires_configured_admin_token(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)

    response = _upload()

    assert response.status_code == 503


def test_import_snapshot_requires_valid_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")

    response = _upload(headers={"X-Admin-Token": "wrong-token"})

    assert response.status_code == 401


def test_import_snapshot_creates_isolated_tenant_collection(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")

    def fake_import(snapshot_bytes, snapshot_filename, tenant_id, knowledge_type):
        assert snapshot_bytes == b"snapshot-data"
        assert snapshot_filename == "playbook.snapshot"
        assert tenant_id == "acme"
        assert knowledge_type == "enterprise_playbook"
        return KnowledgeImportResult("tenant_acme_playbook", len(snapshot_bytes))

    monkeypatch.setattr("app.main.import_snapshot", fake_import)

    response = _upload()

    assert response.status_code == 201
    assert response.json() == {
        "collection_name": "tenant_acme_playbook",
        "bytes_received": len(b"snapshot-data"),
    }


def test_import_snapshot_rejects_public_law_collection_type(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")

    response = _upload(knowledge_type="legal_laws")

    assert response.status_code == 400
    assert "knowledge_type" in response.json()["detail"]


def test_import_snapshot_does_not_overwrite_existing_collection(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")

    def fake_import(*_args):
        raise KnowledgeImportConflict("Collection 'tenant_acme_playbook' already exists.")

    monkeypatch.setattr("app.main.import_snapshot", fake_import)

    response = _upload()

    assert response.status_code == 409
