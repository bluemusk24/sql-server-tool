# SQL Server Diagnostic Assistant

An on-demand, **read-only** assistant that helps DBAs diagnose SQL Server
and application performance incidents. A DBA signs in, enters a SQL Server
connection, reviews and approves the diagnostic query batch, and receives
evidence, likely causes, validation checks, and remediation guidance.

## Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic, SQLAlchemy + Alembic,
  `pyodbc` (Microsoft ODBC Driver 18), arq + Redis, Pandas/Polars,
  Authlib, WeasyPrint.
- **Frontend:** React + TypeScript, Vite, React Router, Vitest.

## Project layout

```
backend/    # FastAPI application, tests, migrations
docs/       # spec.md, process.md, team docs
frontend/   # React + TypeScript SPA
openapi.yaml
AGENTS.md
```

## Backend

```bash
cd backend
uv sync                        # install dependencies
uv run pytest                  # run the test suite
uv run ruff check .            # lint
uv run uvicorn app.main:app --reload   # run the API (default :8000)
arq app.worker.WorkerSettings          # run the background worker
```

Configuration comes from environment variables (see `backend/.env.example`);
`.env` is git-ignored and never committed.

## Frontend

```bash
cd frontend
npm install        # install dependencies
npm run dev        # start the Vite dev server (http://localhost:5173)
npm run build      # type-check and build for production
npm test           # run the Vitest suite
```

The frontend calls the API at `VITE_API_BASE_URL` (see
`frontend/.env.example`); the Vite dev server proxies API paths
(`/health`, `/auth`, `/investigations`, `/jobs`, `/reports`) to
`http://localhost:8000`.

## Documentation

- `docs/spec.md` — product scope (POC).
- `docs/process.md` — how work is organized (roles and lifecycle).
- `docs/team/` — agent roles (PM, engineer, QA).
- Tasks are GitHub issues in this repository.
