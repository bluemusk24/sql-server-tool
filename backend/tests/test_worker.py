"""Unit tests for the arq worker functions and worker settings (#11).

The worker is exercised with dependency-injected fakes: no Redis and no live
SQL Server are needed. The real Redis round-trip lives in the gated
``tests/test_jobs_redis.py``.

Importing :mod:`app.worker` loads settings at import time (same fail-fast
pattern as ``app.main``), so it is imported inside the ``worker`` fixture after
the environment is set.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.credentials import AdContext, CredentialStore, SqlCredentials
from app.diagnostics.catalog import Catalog, build_catalog
from app.execution import RunNotApproved
from app.investigations.repository import (
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
)
from app.investigations.service import required_catalog_ids
from app.jobs.queue import task_path
from app.persistence import CleanupReport
from app.sql.connector import Row

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


def _settings(**overrides) -> Settings:
    values = {
        "database_url": VALID_ENV["DATABASE_URL"],
        "redis_url": VALID_ENV["REDIS_URL"],
        "oidc_issuer": VALID_ENV["OIDC_ISSUER"],
        "oidc_client_id": VALID_ENV["OIDC_CLIENT_ID"],
        "oidc_client_secret": VALID_ENV["OIDC_CLIENT_SECRET"],
        "llm_endpoint": VALID_ENV["LLM_ENDPOINT"],
        "llm_api_key": VALID_ENV["LLM_API_KEY"],
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def worker(monkeypatch):
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    import app.worker

    return app.worker


@pytest.fixture
def repository() -> InMemoryInvestigationRepository:
    repo = InMemoryInvestigationRepository()
    repo.create_investigation(
        organization_id=1,
        server_host="sql01",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=1,
        status="draft",
    )
    return repo


# --- example task -----------------------------------------------------------


def test_example_task_returns_marker(worker):
    assert asyncio.run(worker.example_task({})) == worker.EXAMPLE_TASK_MARKER


def test_example_task_sleeps(worker, monkeypatch):
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    asyncio.run(worker.example_task({}))
    assert recorded == [pytest.approx(0.1)]


# --- worker settings --------------------------------------------------------


def test_make_worker_settings_reflects_settings(worker):
    settings = _settings(job_max_tries=4, job_timeout_seconds=45, worker_max_jobs=3)
    built = worker.make_worker_settings(settings)

    assert built["functions"] == [
        worker.example_task,
        worker.diagnostic_run,
        worker.retention_cleanup,
    ]
    assert built["max_tries"] == 4
    assert built["job_timeout"] == 45
    assert built["max_jobs"] == 3
    assert built["retry_jobs"] is True
    assert built["queue_name"] == "arq:queue"
    assert built["redis_settings"].host == "localhost"
    assert built["redis_settings"].port == 6379
    assert built["redis_settings"].database == 0


def test_retention_cleanup_task_delegates_to_the_persistence_repository(
    worker,
):
    class FakePersistence:
        def __init__(self) -> None:
            self.cleaned_up = False

        def cleanup(self, *, now=None):
            self.cleaned_up = True
            return CleanupReport(
                deleted={"evidence_run": 1, "finding": 1, "audit": 1}
            )

    fake = FakePersistence()
    result = asyncio.run(
        worker.retention_cleanup({"persistence_repository": fake})
    )

    assert fake.cleaned_up is True
    assert result == {
        "deleted": {"evidence_run": 1, "finding": 1, "audit": 1},
        "total": 3,
    }


def test_worker_settings_pass_retry_and_backoff_policy(worker):
    settings = _settings(job_retry_jobs=False, job_max_tries=2)
    built = worker.make_worker_settings(settings)
    assert built["max_tries"] == 2
    assert built["retry_jobs"] is False


def test_worker_settings_inject_fakes(worker, repository):
    store = CredentialStore()
    built = worker.make_worker_settings(
        _settings(), credential_store=store, investigation_repository=repository
    )
    assert built["ctx"]["credential_store"] is store
    assert built["ctx"]["investigation_repository"] is repository


def test_every_worker_task_lives_in_app_worker(worker):
    built = worker.make_worker_settings(_settings())
    for task in built["functions"]:
        assert task.__module__ == "app.worker"
        assert task_path(task) == f"app.worker.{task.__qualname__}"


def test_task_path_rejects_a_task_outside_app_worker():
    async def foreign_task(ctx):  # pragma: no cover - never awaited
        return None

    with pytest.raises(AssertionError):
        task_path(foreign_task)


# --- diagnostic_run ---------------------------------------------------------


class _FakeConnection:
    def __init__(self, database: str) -> None:
        self.database = database

    def close(self) -> None:
        pass


class _RecordingConnector:
    """Minimal #6-shaped connector: one canned row for every query."""

    def __init__(self, rows_by_sql: dict[str, list[Row]] | None = None) -> None:
        self.rows_by_sql = rows_by_sql or {}
        self.connect_calls: list[dict] = []
        self.query_calls: list[dict] = []

    def connect(self, host, database, username, password, *, port=1433, login_timeout=15, query_timeout=30, read_only=True):
        self.connect_calls.append(
            {"database": database, "read_only": read_only, "host": host}
        )
        return _FakeConnection(database)

    def query(self, conn, sql, *, params=None, allow_write=False, timeout=None):
        self.query_calls.append({"sql": sql, "allow_write": allow_write})
        return list(self.rows_by_sql.get(sql, []))


def _approve_all(approvals: InMemoryApprovalRepository, investigation_id: int) -> None:
    for catalog_id in required_catalog_ids(build_catalog()):
        approvals.append_decision(
            investigation_id=investigation_id,
            catalog_id=catalog_id,
            decision="approved",
            actor="dba",
            note=None,
        )


def _ctx(repository, store, *, approvals=None, catalog=None, connector=None) -> dict:
    return {
        "investigation_repository": repository,
        "credential_store": store,
        "approval_repository": approvals or InMemoryApprovalRepository(),
        "catalog": catalog or build_catalog(),
        "connector": connector or _RecordingConnector(),
    }


def _run(worker, repository, store, investigation_id, **ctx_kwargs):
    return asyncio.run(
        worker.diagnostic_run(
            _ctx(repository, store, **ctx_kwargs), investigation_id
        )
    )


def test_diagnostic_run_without_credentials_fails_with_clear_status(
    worker, repository
):
    store = CredentialStore()
    investigation_id = repository.rows[0].id

    with pytest.raises(worker.TaskCredentialsMissing) as exc_info:
        _run(worker, repository, store, investigation_id)

    message = str(exc_info.value)
    assert str(investigation_id) in message
    assert "credentials" in message.lower()
    assert repository.get_investigation(investigation_id).status == "failed"


def test_diagnostic_run_without_approval_fails_and_reflects_status(
    worker, repository
):
    store = CredentialStore()
    investigation_id = repository.rows[0].id
    store.store(str(investigation_id), SqlCredentials("dba", "s3cret"))

    with pytest.raises(RunNotApproved):
        _run(worker, repository, store, investigation_id)

    assert repository.get_investigation(investigation_id).status == "failed"


def test_diagnostic_run_with_credentials_succeeds_and_reflects_status(
    worker, repository
):
    store = CredentialStore()
    investigation_id = repository.rows[0].id
    store.store(str(investigation_id), SqlCredentials("dba", "s3cret"))
    approvals = InMemoryApprovalRepository()
    _approve_all(approvals, investigation_id)

    result = _run(worker, repository, store, investigation_id, approvals=approvals)

    assert result["investigation_id"] == investigation_id
    assert result["status"] == "succeeded"
    assert len(result["sources"]) == len(build_catalog().all())
    assert repository.get_investigation(investigation_id).status == "succeeded"


def test_diagnostic_run_rejects_ad_context_for_now(worker, repository):
    store = CredentialStore()
    investigation_id = repository.rows[0].id
    store.store(
        str(investigation_id),
        AdContext(keytab_path="/secret", principal="p", spn="MSSQLSvc/x"),
    )
    with pytest.raises(worker.TaskCredentialsMissing):
        _run(worker, repository, store, investigation_id)


def test_diagnostic_run_result_carries_no_credential_material(worker, repository):
    store = CredentialStore()
    investigation_id = repository.rows[0].id
    secret = "sup3r-s3cret-password"
    store.store(str(investigation_id), SqlCredentials("dba", secret))
    approvals = InMemoryApprovalRepository()
    _approve_all(approvals, investigation_id)

    result = _run(worker, repository, store, investigation_id, approvals=approvals)

    assert secret not in str(result)
    assert "dba" not in str(result)
    assert set(result) == {"investigation_id", "status", "evidence", "sources"}


def test_diagnostic_run_engine_receives_read_only_connector(worker, repository):
    store = CredentialStore()
    investigation_id = repository.rows[0].id
    store.store(str(investigation_id), SqlCredentials("dba", "s3cret"))
    approvals = InMemoryApprovalRepository()
    _approve_all(approvals, investigation_id)
    connector = _RecordingConnector()

    _run(worker, repository, store, investigation_id, approvals=approvals, connector=connector)

    assert connector.connect_calls
    assert all(call["read_only"] is True for call in connector.connect_calls)
    assert all(call["allow_write"] is False for call in connector.query_calls)


def test_worker_settings_include_execution_context(worker, repository):
    settings = _settings()
    built = worker.make_worker_settings(
        settings,
        investigation_repository=repository,
        catalog=Catalog(),
        connector=_RecordingConnector(),
    )
    assert built["ctx"]["approval_repository"] is not None
    assert built["ctx"]["catalog"] is not None
    assert built["ctx"]["connector"] is not None