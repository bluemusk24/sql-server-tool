# AGENTS.md

SQL Server Diagnostic Assistant (MVP) — an on-demand, **read-only** assistant that
helps DBAs diagnose SQL Server incidents. Product scope lives in `_docs/plan.md`;
how work is organized lives in `_docs/process.md`.

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
- Frontend: React + TypeScript.

## Commands

- `uv sync` - install dependencies
- `uv run pytest` - the whole suite
- `uv run pytest tests/test_home.py` - one test file
- `uv run ruff check .` - lint

## Rules

- Dependencies are added in `pyproject.toml`. Do not add one without asking.
- Work one issue at a time; read the acceptance criteria before starting and
  before closing.
- Commit regularly.
- Credentials are session-only: never log, persist, or commit secrets. `.env`
  is git-ignored.
- Diagnostics are read-only and never execute remediation.
