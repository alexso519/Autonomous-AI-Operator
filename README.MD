# Autonomous AI Operator

An end-to-end workspace for running an autonomous AI operator with:
- A FastAPI backend for orchestration, execution, and runtime services
- A Next.js frontend for chat, operator controls, and benchmark views
- Optional local LLM inference through Ollama

## Tech Stack

- Backend: Python, FastAPI, Uvicorn, SQLite
- Frontend: Next.js 14, React 18, TypeScript, Tailwind CSS
- Local model runtime: Ollama (`qwen2.5` family in default config)
- Containerized development: Docker Compose

## Project Structure

- `backend/` - API server, execution runtime, intelligence and self-improvement modules, tests
- `frontend/` - Next.js app and UI components
- `docs/` - operational and production documentation
- `k8s/` - Kubernetes manifests for deployment scenarios
- `docker-compose.yml` - local development stack

## Prerequisites

Choose one setup path:

### Option A: Docker (recommended)
- Docker Desktop (or Docker Engine + Compose)

### Option B: Manual local run
- Python 3.11+ (3.12 works in this repo)
- Node.js 18+ and npm
- Ollama installed and running (optional but recommended)

## Quick Start (Docker)

1. From the repository root, build and run:

```bash
docker compose up --build
```

2. Open the apps:
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- Backend health: `http://localhost:8000/api/health`
- Ollama API: `http://localhost:11434`

The compose stack also includes an `ollama-init` service that pulls `qwen2.5:3b`.

## Quick Start (Manual)

### 1) Configure environment

Create a local env file from the example:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 2) Run backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

PowerShell activate command:

```powershell
.venv\Scripts\Activate.ps1
```

### 3) Run frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

### 4) (Optional) Pull local model

```bash
ollama pull qwen2.5:3b
```

## Useful Scripts

Frontend:
- `npm run dev` - start development server
- `npm run build` - production build
- `npm run start` - run production server
- `npm run lint` - lint frontend code

Backend:
- `uvicorn app.main:app --reload` - start API in dev mode

## Environment Configuration

Core variables are documented in `.env.example`, including:
- API URLs and ports
- Ollama model selection
- Memory and retention settings
- Computer-use runtime toggles
- Distributed/enterprise feature flags
- Self-improvement feature gates

Start with defaults, then tune by environment.

## Notes

- The repository may contain generated Python cache artifacts (`__pycache__`, `.pyc`) during development; these are not runtime requirements.
- For production deployment guidance, see files in `docs/` and `k8s/`.
