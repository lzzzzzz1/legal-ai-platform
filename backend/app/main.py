import os
import secrets

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.review import ReviewResponse
from app.services.docx_modifier import modify_docx_inplace, parse_modifications
from app.services.docx_parser import extract_docx_text
from app.services.knowledge_import import (
    KnowledgeImportConflict,
    KnowledgeImportError,
    import_snapshot,
)
from app.services.openai_review import review_contract_text

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_KNOWLEDGE_SNAPSHOT_BYTES = int(
    os.getenv("MAX_KNOWLEDGE_SNAPSHOT_BYTES", str(250 * 1024 * 1024))
)

app = FastAPI(
    title="Legal AI Platform API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _require_admin_token(x_admin_token: str | None) -> None:
    configured_token = os.getenv("ADMIN_API_TOKEN")
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge import is not configured. Set ADMIN_API_TOKEN first.",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token.",
        )


@app.post("/api/admin/knowledge/snapshots", status_code=status.HTTP_201_CREATED)
async def import_knowledge_snapshot(
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    knowledge_type: str = Form(...),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, int | str]:
    _require_admin_token(x_admin_token)

    if not file.filename or not file.filename.lower().endswith(".snapshot"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .snapshot files are supported.",
        )

    snapshot_bytes = await file.read(MAX_KNOWLEDGE_SNAPSHOT_BYTES + 1)
    if len(snapshot_bytes) > MAX_KNOWLEDGE_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Snapshot file exceeds the configured size limit.",
        )

    try:
        result = await run_in_threadpool(
            import_snapshot,
            snapshot_bytes,
            file.filename,
            tenant_id,
            knowledge_type,
        )
    except KnowledgeImportConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KnowledgeImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "collection_name": result.collection_name,
        "bytes_received": result.bytes_received,
    }


@app.post("/api/review", response_model=ReviewResponse)
async def review_contract(file: UploadFile = File(...)) -> ReviewResponse:
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files are supported.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file must be 10 MB or smaller.",
        )

    try:
        contract_text = extract_docx_text(file_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse .docx file: {exc}",
        ) from exc

    if not contract_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No readable text was found in the document.",
        )

    review = await run_in_threadpool(
        review_contract_text,
        contract_text=contract_text,
        filename=file.filename,
    )
    review.contract_text = contract_text
    return review


@app.post("/api/export")
async def export_reviewed_contract(
    file: UploadFile = File(...),
    modifications: str = Form(...),
) -> StreamingResponse:
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files are supported.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file must be 10 MB or smaller.",
        )

    try:
        parsed_modifications = parse_modifications(modifications)
        reviewed_docx = await run_in_threadpool(
            modify_docx_inplace,
            file_bytes,
            parsed_modifications,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to export reviewed .docx file: {exc}",
        ) from exc

    return StreamingResponse(
        iter([reviewed_docx]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="reviewed_contract.docx"'},
    )
