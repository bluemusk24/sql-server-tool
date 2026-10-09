# SQL Server Diagnostic Assistant

On-demand, read-only diagnostic assistant for SQL Server incidents. Scope lives
in `_docs/plan.md`; how work is organized lives in `_docs/process.md`.

## Run locally

```sh
uv sync
cp .env.example .env   # then fill in DATABASE_URL, REDIS_URL, OIDC_*, LLM_*
uv run uvicorn app.main:app --reload
```

`GET /health` returns service status. Config is read from the environment with
`.env` as the base; missing required settings fail fast at startup.

## Checks

- `uv run pytest` - the whole suite
- `uv run ruff check .` - lint