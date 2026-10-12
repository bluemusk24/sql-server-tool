"""Tests for the run-start and job-status endpoints (#11).

Exercised with FastAPI's ``TestClient`` and dependency overrides: an in-memory
investigation/approval repository stands in for SQL Server and a recording
queue stands in for Redis, so no live infrastructure is needed. The real Redis
round-trip lives in the gated ``tests/test_jobs_redis.py``.
"""

from __future__ import annotations

import sys

import pytest

from app.diagnostics.catalog import build_catalog
from app.investigations.dependencies import (
    FIXTURE_SSO_ISSUER,
    get_approval_repository,
    get_audit_writer,
    get_catalog,
    get_investigation_repository,
)
from app.investigations.repository import (
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
    RecordingAuditWriter,
)
from app.investigations.service import required_catalog_ids
from app.jobs.dependencies import get_job_queue, get_job_status_reader
from app.jobs.queue import RecordingJobQueue, task_path
from app.jobs.status import JobNotFound, JobState, JobStatusResponse

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}

FIXTURE_ORG_ID = 7


def _settings():
    from app.config import Settings

    return Settings(
        database_url=VALID_ENV["DATABASE_URL"],
        redis_url=VALID_ENV["REDIS_URL"],
        oidc_issuer=VALID_ENV["OIDC_ISSUER"],
        oidc_client_id=VALID_ENV["OIDC_CLIENT_ID"],
        oidc_client_secret=VALID_ENV["OIDC_CLIENT_SECRET"],
        llm_endpoint=VALID_ENV["LLM_ENDPOINT"],
        llm_api_key=VALID_ENV["LLM_API_KEY"],
    )


class FakeStatusReader:
    """Returns canned statuses; records what was asked for."""

    def __init__(self) -> None:
        self.responses: dict[str, JobStatusResponse] = {}
        self.requests: list[str] = []

    def set(self, response: JobStatusResponse) -> None:
        self.responses[response.job_id] = response

    async def get_status(self, job_id: str) -> JobStatusResponse:
        self.requests.append(job_id)
        if job_id not in self.responses:
            raise JobNotFound(f"no job with id {job_id!r}")
        return self.responses[job_id]


@pytest.fixture
def repository() -> InMemoryInvestigationRepository:
    return InMemoryInvestigationRepository({FIXTURE_SSO_ISSUER: FIXTURE_ORG_ID})


@pytest.fixture
def approvals() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def queue() -> RecordingJobQueue:
    return RecordingJobQueue()


@pytest.fixture
def reader() -> FakeStatusReader:
    return FakeStatusReader()


@pytest.fixture
def client(monkeypatch, repository, approvals, audit, queue, reader):
    from fastapi.testclient import TestClient

    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app

    app = create_app(_settings())
    app.dependency_overrides[get_investigation_repository] = lambda: repository
    app.dependency_overrides[get_approval_repository] = lambda: approvals
    app.dependency_overrides[get_audit_writer] = lambda: audit
    app.dependency_overrides[get_catalog] = lambda: build_catalog()
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_job_status_reader] = lambda: reader
    return TestClient(app)


def _seed_investigation(repository, *, status: str = "draft") -> int:
    record = repository.create_investigation(
        organization_id=FIXTURE_ORG_ID,
        server_host="sql01",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=1,
        status=status,
    )
    return record.id


def _approve_all(approvals, investigation_id: int) -> None:
    for catalog_id in required_catalog_ids(build_catalog()):
        approvals.append_decision(
            investigation_id=investigation_id,
            catalog_id=catalog_id,
            decision="approved",
            actor="dba",
            note=None,
        )


# --- POST /investigations/{id}/run -----------------------------------------


def test_run_unknown_investigation_is_404(client, queue):
    response = client.post("/investigations/999/run")
    assert response.status_code == 404
    assert queue.enqueued == []


def test_run_without_full_approval_is_409_and_enqueues_nothing(
    client, repository, queue
):
    investigation_id = _seed_investigation(repository)
    response = client.post(f"/investigations/{investigation_id}/run")
    assert response.status_code == 409
    assert "approved" in response.text
    assert queue.enqueued == []
    assert repository.get_investigation(investigation_id).status == "draft"


def test_run_with_rejected_query_is_409(client, repository, approvals, queue):
    investigation_id = _seed_investigation(repository)
    _approve_all(approvals, investigation_id)
    approvals.append_decision(
        investigation_id=investigation_id,
        catalog_id=required_catalog_ids(build_catalog())[0],
        decision="rejected",
        actor="dba",
        note=None,
    )
    response = client.post(f"/investigations/{investigation_id}/run")
    assert response.status_code == 409
    assert queue.enqueued == []


def test_run_enqueues_diagnostic_run_and_returns_202_with_job_id(
    client, repository, approvals, queue
):
    investigation_id = _seed_investigation(repository)
    _approve_all(approvals, investigation_id)

    response = client.post(f"/investigations/{investigation_id}/run")

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert job_id == "fake-job-1"
    assert len(queue.enqueued) == 1
    task, args, kwargs = queue.enqueued[0]
    assert task_path(task) == "app.worker.diagnostic_run"
    assert args == (investigation_id,)
    assert kwargs == {}
    assert repository.get_investigation(investigation_id).status == "running"


def test_run_payload_carries_no_credentials(client, repository, approvals, queue):
    investigation_id = _seed_investigation(repository)
    _approve_all(approvals, investigation_id)

    client.post(f"/investigations/{investigation_id}/run")

    task, args, kwargs = queue.enqueued[0]
    rendered = f"{task_path(task)} {args!r} {kwargs!r}"
    assert set(kwargs) == set()
    assert args == (investigation_id,)
    for word in ("password", "pwd", "secret", "username", "connection"):
        assert word not in rendered.lower()


def test_second_run_is_409(client, repository, approvals, queue):
    investigation_id = _seed_investigation(repository)
    _approve_all(approvals, investigation_id)

    first = client.post(f"/investigations/{investigation_id}/run")
    second = client.post(f"/investigations/{investigation_id}/run")

    assert first.status_code == 202
    assert second.status_code == 409
    assert len(queue.enqueued) == 1


# --- GET /jobs/{job_id} -----------------------------------------------------


def _status(job_id: str, state: JobState, **extra) -> JobStatusResponse:
    return JobStatusResponse(job_id=job_id, status=state, **extra)


def test_get_queued_job(client, reader):
    reader.set(_status("job-1", JobState.queued, investigation_id=7))
    response = client.get("/jobs/job-1")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job-1"
    assert body["status"] == "queued"
    assert body["investigation_id"] == 7
    assert body["result"] is None
    assert body["error"] is None


def test_get_running_job(client, reader):
    reader.set(_status("job-2", JobState.running))
    response = client.get("/jobs/job-2")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_get_succeeded_job_includes_result(client, reader):
    reader.set(_status("job-3", JobState.succeeded, result={"marker": "ok"}))
    response = client.get("/jobs/job-3")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"] == {"marker": "ok"}
    assert body["error"] is None


def test_get_failed_job_includes_error(client, reader):
    reader.set(_status("job-4", JobState.failed, error="boom"))
    response = client.get("/jobs/job-4")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "boom"
    assert body["result"] is None


def test_get_unknown_job_is_404(client, reader):
    response = client.get("/jobs/does-not-exist")
    assert response.status_code == 404
    assert reader.requests == ["does-not-exist"]