import os
import re
from dataclasses import dataclass

import httpx
from qdrant_client import QdrantClient


TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
KNOWLEDGE_TYPE_SUFFIXES = {
    "enterprise_playbook": "playbook",
    "contract_templates": "templates",
}


class KnowledgeImportError(Exception):
    """Raised when a tenant knowledge-base snapshot cannot be imported."""


class KnowledgeImportConflict(KnowledgeImportError):
    """Raised when an import would overwrite an existing collection."""


@dataclass(frozen=True)
class KnowledgeImportResult:
    collection_name: str
    bytes_received: int


def build_tenant_collection_name(tenant_id: str, knowledge_type: str) -> str:
    normalized_tenant_id = tenant_id.strip().lower()
    if not TENANT_ID_PATTERN.fullmatch(normalized_tenant_id):
        raise KnowledgeImportError(
            "tenant_id must contain 1-63 letters, numbers, underscores, or hyphens."
        )

    suffix = KNOWLEDGE_TYPE_SUFFIXES.get(knowledge_type)
    if not suffix:
        allowed = ", ".join(KNOWLEDGE_TYPE_SUFFIXES)
        raise KnowledgeImportError(f"knowledge_type must be one of: {allowed}.")

    return f"tenant_{normalized_tenant_id}_{suffix}"


def _qdrant_client() -> QdrantClient:
    api_key = os.getenv("QDRANT_API_KEY") or None
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=api_key,
    )


def import_snapshot(
    snapshot_bytes: bytes,
    snapshot_filename: str,
    tenant_id: str,
    knowledge_type: str,
) -> KnowledgeImportResult:
    if not snapshot_bytes:
        raise KnowledgeImportError("Snapshot file is empty.")

    collection_name = build_tenant_collection_name(tenant_id, knowledge_type)
    client = _qdrant_client()
    if client.collection_exists(collection_name):
        raise KnowledgeImportConflict(
            f"Collection '{collection_name}' already exists. Use a new tenant ID or remove it through an audited admin workflow."
        )

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    headers: dict[str, str] = {}
    api_key = os.getenv("QDRANT_API_KEY")
    if api_key:
        headers["api-key"] = api_key

    timeout_seconds = float(os.getenv("KNOWLEDGE_IMPORT_TIMEOUT_SECONDS", "300"))
    response = httpx.post(
        f"{qdrant_url}/collections/{collection_name}/snapshots/upload?priority=snapshot",
        headers=headers,
        files={
            "snapshot": (
                snapshot_filename,
                snapshot_bytes,
                "application/octet-stream",
            )
        },
        timeout=timeout_seconds,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise KnowledgeImportError(f"Qdrant rejected the snapshot: {exc.response.text}") from exc

    return KnowledgeImportResult(
        collection_name=collection_name,
        bytes_received=len(snapshot_bytes),
    )
