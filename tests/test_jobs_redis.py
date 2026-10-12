"""End-to-end queue round-trip against a live Redis (#11).

Gated by ``RUN_REDIS_TESTS=1`` and skipped visibly when Redis is not reachable,
so the default unit suite runs with no Redis. CI must run with Redis: set
``RUN_REDIS_TESTS=1`` and point ``REDIS_URL`` at a live broker.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest

RUN_REDIS_TESTS = os.environ.get("RUN_REDIS_TESTS") == "1"

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


def _skip_reason() -> str | None:
    if not RUN_REDIS_TESTS:
        return (
            "Redis tests disabled: set RUN_REDIS_TESTS=1 with a live Redis "
            "reachable via REDIS_URL to run the queue round-trip test"
        )
    if importlib.util.find_spec("redis") is None:
        return "redis client not installed"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _reachable(redis_url: str) -> bool:
    from arq.connections import RedisSettings, create_pool
    from redis.exceptions import RedisError

    async def _ping() -> bool:
        pool = await create_pool(RedisSettings.from_dsn(redis_url))
        try:
            await pool.ping()
            return True
        finally:
            await pool.close()

    try:
        return asyncio.run(_ping())
    except (OSError, RedisError, TimeoutError):
        return False


def test_example_task_round_trip_via_get_jobs(monkeypatch):
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)

    from arq.worker import create_worker
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.credentials import CredentialStore
    from app.investigations.repository import InMemoryInvestigationRepository
    from app.jobs.queue import ArqJobQueue
    from app.jobs.status import ArqJobStatusReader, JobState
    from app.main import create_app
    from app.worker import EXAMPLE_TASK_MARKER, example_task, make_worker_settings

    settings = load_settings()
    if not _reachable(settings.redis_url):
        pytest.skip(f"no Redis reachable at {settings.redis_url}")

    async def scenario() -> tuple[str, object]:
        queue = ArqJobQueue(settings.redis_url)
        job = await queue.enqueue_job(example_task)

        worker = create_worker(
            make_worker_settings(
                settings,
                credential_store=CredentialStore(),
                investigation_repository=InMemoryInvestigationRepository(),
            ),
            burst=True,
            handle_signals=False,
        )
        try:
            await worker.async_run()
        finally:
            await worker.close()

        reader = ArqJobStatusReader(settings.redis_url)
        return job.job_id, await reader.get_status(job.job_id)

    job_id, status = asyncio.run(scenario())
    assert status.status is JobState.succeeded
    assert status.result == EXAMPLE_TASK_MARKER

    client = TestClient(create_app(settings))
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"] == EXAMPLE_TASK_MARKER