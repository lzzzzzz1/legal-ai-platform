# Legal AI Platform MVP

MVP for an AI legal assistant platform:

- Upload a `.docx` contract.
- Extract plain text on the FastAPI backend.
- Review the contract with the OpenAI API.
- Display structured risk cards in a React UI.

## Backend

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

Open `http://localhost:5173`. The frontend container serves the built React app
with Nginx and proxies API traffic to the backend container.

