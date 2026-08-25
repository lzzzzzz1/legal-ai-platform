# Legal AI Platform MVP

MVP for an AI legal assistant platform:

- Upload a `.docx` or `.pdf` contract.
- Extract DOCX text locally or PDF markdown through the configured PDF parser.
- Review the contract with the OpenAI API.
- Display structured risk cards in a React UI.

## Backend

For local development, authentication is disabled by default. For a deployed
instance, set `APP_ENV=production` and configure `API_AUTH_TOKEN`; clients must
send `X-API-Token` and `X-Tenant-ID` on review and export requests. The frontend
can supply these through `VITE_API_AUTH_TOKEN` and `VITE_TENANT_ID` for a
controlled internal deployment. Do not expose a long-lived API token in a
public frontend; use an authenticated gateway in that case.

The chat model and embedding model may use different OpenAI-compatible
endpoints. Set `BAILIAN_BASE_URL` for chat and
`BAILIAN_EMBEDDING_BASE_URL` for embeddings.

PDF parsing and retrieval reranking are configured separately:
`PDF_PARSE_URL` points to the PDF parser, while `RERANK_URL` points to the
OpenAI-compatible `/v1/rerank` endpoint. Vector search recalls `RAG_RECALL_K`
items and reranks the final results with `RERANK_MODEL`. If either service is
unavailable, the backend falls back to local DOCX parsing or vector scores.
PDF review is supported, but Word tracked-change export remains DOCX-only.
For large or scanned PDFs, the frontend proxy permits up to six minutes for
OCR/parse completion; the result will still show a text-quality warning when
the extracted content is incomplete.

```powershell
cd backend
Copy-Item .env.example .env
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run backend tests with:

```powershell
cd backend
pip install -r requirements-dev.txt
cd ..
python -m pytest backend
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` and `/health` to `http://localhost:8000`.

## Docker

Create `backend/.env` from `backend/.env.example` and fill `DASHSCOPE_API_KEY`, then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. The frontend container serves the built React app
with Nginx and proxies API traffic to the backend container.

When developing the frontend locally, keep `http://localhost:5173` reserved for
Vite:

```powershell
cd frontend
npm.cmd run dev
```

The Docker frontend container defaults to `http://localhost:8080`, keeping it
separate from the local Vite development server at port 5173. Set
`FRONTEND_PORT` before `docker compose up` if another port is required.

## Law Ingestion

Start local Qdrant:

```powershell
docker compose up -d qdrant
```

Ingest the sample Civil Code contract articles:

```powershell
cd backend
python scripts/ingest_laws.py --source-file tests/data/civil_code_sample.txt
```

To import the expanded SQLite legal knowledge base, first start Qdrant and
ensure `DASHSCOPE_API_KEY` is configured. This creates the isolated collection
`legal_laws_v2_1024` and does not overwrite the current `legal_laws` collection:

```powershell
cd backend
python scripts/ingest_sqlite_laws.py `
  --database "C:\path\to\法规知识库_v2.sqlite" `
  --collection legal_laws_v2_1024
```

Run a small smoke test before the full import:

```powershell
python scripts/ingest_sqlite_laws.py `
  --database "C:\path\to\法规知识库_v2.sqlite" `
  --collection legal_laws_v2_1024 `
  --limit 32
```

After validating retrieval quality, switch `QDRANT_COLLECTION` in
`backend/.env` to `legal_laws_v2_1024` and restart the backend. The importer
preserves law title, article/paragraph location, page range, legal metadata,
effectiveness status, source path, and embedding model metadata in each point.

