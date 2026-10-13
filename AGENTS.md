# AGENTS.md

SQL Server Diagnostic Assistant (MVP) — an on-demand, **read-only** assistant that
helps DBAs diagnose SQL Server incidents. Product scope lives in `docs/spec.md`;
how work is organized lives in `docs/process.md`.

## Where the work lives

The backlog is GitHub issues in `bluemusk24/sql-server-tool`. At the start of a
session, find the task there instead of inventing one:

- `gh issue list --repo bluemusk24/sql-server-tool --state open`
- `gh issue view <n> --repo bluemusk24/sql-server-tool`

Work one issue at a time. Read its Goal/Description before starting and again
before closing.

## Stack

- Backend: Python 3.11+, FastAPI, Pydantic, SQLAlchemy + Alembic, `pyodbc`
  (Microsoft ODBC Driver 18), arq + Redis, Pandas/Polars, Authlib, WeasyPrint.
- Frontend: React + TypeScript, Vite, React Router, Vitest.

## Project layout

```
backend/    # FastAPI application, tests, migrations
docs/       # spec.md, process.md, team docs
frontend/   # React + TypeScript SPA
openapi.yaml
AGENTS.md
```

## Commands

### Backend (from `backend/`)

- `uv sync` - install dependencies
- `uv add <PACKAGE-NAME>` - add a package
- `uv run python <PYTHON-FILE>` - run a Python file
- `uv run pytest` - the whole suite
- `uv run ruff check .` - lint
- `uv run uvicorn app.main:app --reload` - start the API

### Frontend (from `frontend/`)

- `npm install` - install dependencies
- `npm run dev` - start Vite dev server (http://localhost:5173)
- `npm run build` - type-check and build for production
- `npm test` - run the Vitest suite

## Rules

- Dependencies are added in `pyproject.toml` (backend) or `package.json`
  (frontend). Do not add one without asking.
- Work one issue at a time; read the acceptance criteria before starting and
  before closing.
- Commit regularly.
- Credentials are session-only: never log, persist, or commit secrets. `.env`
  is git-ignored.
- Diagnostics are read-only and never execute remediation.
