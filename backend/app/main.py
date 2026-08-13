import os
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.review import DeepReviewRequest, DocumentQuality, ReviewFeedback, ReviewResponse, TextReviewRequest
from app.services.docx_modifier import modify_docx_inplace, parse_modifications
from app.services.docx_parser import extract_docx_text
from app.services.pdf_parser import extract_pdf_document
from app.services.knowledge_import import (
    KnowledgeImportConflict,
    KnowledgeImportError,
    import_snapshot,
)
from app.services.openai_review import review_contract_text
from app.services.deep_review import review_contract_deeply
from app.services.review_report import render_review_report
from app.services.request_auth import require_request_identity

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
API_VERSION = "2026.08.13-deep-review"
MAX_KNOWLEDGE_SNAPSHOT_BYTES = int(
    os.getenv("MAX_KNOWLEDGE_SNAPSHOT_BYTES", str(250 * 1024 * 1024))
)
REVIEW_SCOPE_NAMES = {
    "基础质量与合同框架",
    "主体与签约权限", "合同成立与效力", "标的与价格", "付款与发票", "交付与验收",
    "质量与售后", "违约与责任", "解除与终止", "知识产权", "保密与数据",
    "合规与许可", "通知与送达", "争议解决", "附件与文本一致性",
}

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
    return {"status": "ok", "api_version": API_VERSION}


@app.get("/api/system-status")
def system_status(
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> dict[str, object]:
    """Return configuration health without exposing credentials or document data."""
    require_request_identity(x_api_token, x_tenant_id)

    def configured_url(name: str) -> dict[str, object]:
        value = os.getenv(name, "")
        parsed = urlparse(value)
        return {"endpoint_configured": bool(value), "host": parsed.netloc or None}

    return {
        "status": "ok",
        "api_version": API_VERSION,
        "review_model": {
            "configured": bool(os.getenv("DASHSCOPE_API_KEY") and os.getenv("BAILIAN_MODEL")),
            "model": os.getenv("BAILIAN_MODEL") or None,
            **configured_url("BAILIAN_BASE_URL"),
        },
        "knowledge_base": {
            "configured": bool(os.getenv("QDRANT_COLLECTION")),
            "collection": os.getenv("QDRANT_COLLECTION") or None,
            **configured_url("QDRANT_URL"),
        },
        "pdf_parser": configured_url("PDF_PARSE_URL"),
        "reranker": {"enabled": os.getenv("RERANK_ENABLED", "true").lower() in {"1", "true", "yes"}, **configured_url("RERANK_URL")},
    }


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
async def review_contract(
    file: UploadFile = File(...),
    review_scope: str | None = Form(default=None),
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> ReviewResponse:
    require_request_identity(x_api_token, x_tenant_id)
    if not file.filename or not file.filename.lower().endswith((".docx", ".pdf")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx and .pdf files are supported.",
        )

    file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
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

    document_quality = DocumentQuality(
        kind="pdf" if file.filename.lower().endswith(".pdf") else "docx",
        status="not_applicable" if file.filename.lower().endswith(".docx") else "searchable",
        note="DOCX 使用原生文本解析。" if file.filename.lower().endswith(".docx") else "",
    )
    try:
        if file.filename.lower().endswith(".pdf"):
            parsed_pdf = extract_pdf_document(file_bytes, file.filename)
            contract_text = parsed_pdf.text
            note = {
                "searchable": "PDF 文本可搜索，已完成文本提取。",
                "partial": "PDF 仅部分页面识别出文本，可能存在漏审，需人工复核。",
                "scanned": "PDF 疑似扫描件，当前仅识别到少量文本，需先 OCR 后复核。",
            }[parsed_pdf.status]
            document_quality = DocumentQuality(
                kind="pdf",
                status=parsed_pdf.status,
                pages=parsed_pdf.pages,
                extracted_chars=parsed_pdf.extracted_chars,
                average_chars_per_page=parsed_pdf.average_chars_per_page,
                ocr_detected=parsed_pdf.ocr_detected,
                note=note,
            )
        else:
            contract_text = extract_docx_text(file_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse {Path(file.filename).suffix.lower()} file: {exc}",
        ) from exc

    if not contract_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No readable text was found in the document.",
        )

    selected_scope = None
    if review_scope:
        try:
            decoded_scope = json.loads(review_scope)
            if isinstance(decoded_scope, list):
                selected_scope = list(dict.fromkeys(item for item in decoded_scope if isinstance(item, str)))
                unknown_scopes = [item for item in selected_scope if item not in REVIEW_SCOPE_NAMES]
                if unknown_scopes:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="review_scope contains unsupported review topics.",
                    )
                if not selected_scope:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Select at least one review topic.",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="review_scope must be a JSON array.",
                )
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="review_scope must be a JSON array.",
            )

    review_kwargs = {"contract_text": contract_text, "filename": file.filename}
    if selected_scope is not None:
        review_kwargs["selected_scope"] = selected_scope
    review = await run_in_threadpool(review_contract_text, **review_kwargs)
    review.contract_text = contract_text
    review.document_quality = document_quality
    if document_quality.status == "partial":
        review.warnings.append(document_quality.note)
        review.manual_review_required = True
        if review.review_status == "complete":
            review.review_status = "partial"
    return review


@app.post("/api/review/text", response_model=ReviewResponse)
async def review_contract_text_stage(
    request: TextReviewRequest,
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> ReviewResponse:
    """Run the substantive stage against the user's preflight-corrected text."""
    require_request_identity(x_api_token, x_tenant_id)
    selected_scope = list(dict.fromkeys(request.review_scope))
    unknown_scopes = [item for item in selected_scope if item not in REVIEW_SCOPE_NAMES]
    if unknown_scopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="review_scope contains unsupported review topics.",
        )
    if not selected_scope:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one review topic.",
        )

    review = await run_in_threadpool(
        review_contract_text,
        contract_text=request.contract_text,
        filename=request.filename,
        selected_scope=selected_scope,
    )
    review.contract_text = request.contract_text
    return review


@app.post("/api/review/deep", response_model=ReviewResponse)
async def review_contract_deep_stage(
    request: DeepReviewRequest,
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> ReviewResponse:
    require_request_identity(x_api_token, x_tenant_id)
    return await run_in_threadpool(
        review_contract_deeply,
        contract_text=request.contract_text,
        filename=request.filename,
        settings=request.settings,
    )


@app.post("/api/export")
async def export_reviewed_contract(
    file: UploadFile = File(...),
    modifications: str = Form(...),
    export_mode: str = Form(default="tracked"),
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> StreamingResponse:
    require_request_identity(x_api_token, x_tenant_id)
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .docx files are supported.",
        )

    file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
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
        if export_mode not in {"tracked", "final"}:
            raise ValueError("export_mode must be 'tracked' or 'final'.")
        reviewed_docx = await run_in_threadpool(
            modify_docx_inplace,
            file_bytes,
            parsed_modifications,
            export_mode == "tracked",
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
        headers={
            "Content-Disposition": (
                'attachment; filename="reviewed_contract.docx"'
                if export_mode == "tracked"
                else 'attachment; filename="final_contract.docx"'
            )
        },
    )


@app.post("/api/report", response_class=HTMLResponse)
async def export_review_report(
    review: ReviewResponse = Body(...),
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> HTMLResponse:
    require_request_identity(x_api_token, x_tenant_id)
    return HTMLResponse(render_review_report(review))


@app.post("/api/review/feedback", status_code=status.HTTP_201_CREATED)
async def record_review_feedback(
    feedback: ReviewFeedback,
    x_api_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> dict[str, str]:
    require_request_identity(x_api_token, x_tenant_id)
    path = Path(os.getenv("REVIEW_FEEDBACK_LOG", "logs/review_feedback.jsonl"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    record = {
        **feedback.model_dump(),
        "tenant_id": x_tenant_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "recorded"}
