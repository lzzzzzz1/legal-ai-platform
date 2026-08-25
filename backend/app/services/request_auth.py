import os
import re
import secrets

from fastapi import HTTPException, status


TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")


def _auth_required() -> bool:
    configured = os.getenv("REQUIRE_API_AUTH")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("APP_ENV", "development").strip().lower() in {
        "production",
        "prod",
    }


def require_request_identity(
    x_api_token: str | None,
    x_tenant_id: str | None,
) -> str:
    configured_token = os.getenv("API_AUTH_TOKEN")
    if _auth_required() and not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    if configured_token:
        if not x_api_token or not secrets.compare_digest(x_api_token, configured_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token.",
            )
    elif _auth_required():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    tenant_id = (x_tenant_id or "local").strip().lower()
    if not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID must contain 1-63 letters, numbers, underscores, or hyphens.",
        )
    return tenant_id
