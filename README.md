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

## Background worker

Diagnostic runs are queued to Redis and executed by an arq worker (#11), so the
HTTP request never blocks on a run. Run the worker against the same `REDIS_URL`:

```sh
uv run arq app.worker.WorkerSettings
```

The worker settings object is `app.worker.WorkerSettings`; arq resolves queued
tasks by dotted module path, so every task lives in `app.worker`. Worker retry
and timeout policy is config-driven via `JOB_MAX_TRIES`, `JOB_TIMEOUT_SECONDS`,
`WORKER_MAX_JOBS`, `WORKER_QUEUE_NAME`, and `JOB_RETRY_JOBS`.

## Checks

- `uv run pytest` - the whole suite
- `uv run ruff check .` - lint

Integration tests that need live infrastructure are gated and skip visibly:
`RUN_DB_TESTS=1` for SQL Server and `RUN_REDIS_TESTS=1` for the Redis-backed
queue round-trip.