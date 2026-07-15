from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.schemas.review import ReviewResponse
from app.services.docx_modifier import modify_docx_inplace, parse_modifications
from app.services.docx_parser import extract_docx_text
from app.services.openai_review import review_contract_text

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

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
