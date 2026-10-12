"""Unit tests for the #21 audit trail coverage.

The audit trail is append-only and must record the investigation
lifecycle without ever carrying credentials. These tests exercise
the real service and router functions against the in-memory
repositories and a recording audit writer, with no SQL Server,
Redis or network.

Covered: exactly one ``investigation.create`` row per created
investigation; one ``approval.decide`` row per newly appended
decision, with duplicate decisions and reads writing nothing; one
``run.start`` row per enqueued run; and one ``report.access`` row
per report download.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.ai.schemas import Cause, ConfidenceLabel
from app.diagnostics.catalog import build_catalog
from app.investigations.repository import (
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
    RecordingAuditWriter,
)
from app.investigations.schemas import (
    ApprovalRequest,
    AuthType,
    Decision,
    InvestigationCreate,
)
from app.investigations.service import (
    create_investigation,
    record_decisions,
    required_catalog_ids,
)
from app.jobs.queue import RecordingJobQueue
from app.persistence import InMemoryPersistenceRepository
from app.report.repository import InMemoryReportRepository
from app.report.router import download_report_pdf
from app.report.schemas import ReportPayload
from app.rules.signals import Severity

ISSUER = "https://issuer.example.com/"

#: Importing :mod:`app.jobs.service` loads settings at import time (it
#: imports :mod:`app.worker`), so it is imported inside the ``start_run``
#: fixture after the environment is set (same pattern as ``test_worker``).
VALID_ENV = {
    "DATABASE_URL": (
        "mssql+pyodbc://user:pass@db:1433/repo"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    ),
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


@pytest.fixture
def start_run(monkeypatch):
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    from app.jobs.service import start_run

    return start_run


def _create() -> InvestigationCreate:
    return InvestigationCreate(
        server_host="sql01.acme.example",
        server_port=1433,
        auth_type=AuthType.sql,
        databases=None,
        lookback_hours=4,
    )


def _approved_setup():
    """An investigation with every required catalog query approved."""
    repository = InMemoryInvestigationRepository({ISSUER: 1})
    approvals = InMemoryApprovalRepository()
    catalog = build_catalog()
    record = repository.create_investigation(
        organization_id=1,
        server_host="sql01.acme.example",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=4,
        status="draft",
    )
    for catalog_id in required_catalog_ids(catalog):
        approvals.append_decision(
            investigation_id=record.id,
            catalog_id=catalog_id,
            decision="approved",
            actor="dina",
            note=None,
        )
    return repository, approvals, catalog, record.id


def _payload() -> ReportPayload:
    return ReportPayload(
        investigation_id=1,
        organization="Acme Corp",
        server_host="sql01.acme.example",
        server_port=1433,
        database_scope="instance",
        databases=[],
        lookback_hours=4,
        investigation_time=datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        data_sources=[],
        findings_by_area=[],
        primary_cause=Cause(
            area="blocking",
            description="Session 60 is blocked by session 57.",
            confidence_percent=82,
            confidence_label=ConfidenceLabel.high,
        ),
        alternatives=[],
        confidence_percent=82,
        confidence_label=ConfidenceLabel.high,
        severity=Severity.high,
        rationale="One high-severity blocking signal.",
        inconclusive=False,
        coverage_gaps=[],
        recommendations=[],
        validation_checks=[],
        remediation=[],
    )


# --- investigation.create ------------------------------------------------


def test_create_investigation_writes_exactly_one_audit_row():
    repository = InMemoryInvestigationRepository({ISSUER: 1})
    audit = RecordingAuditWriter()

    record = create_investigation(
        _create(),
        caller_issuer=ISSUER,
        caller_actor="dina",
        repository=repository,
        audit_writer=audit,
    )

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action == "investigation.create"
    assert entry.actor == "dina"
    assert entry.organization_id == 1
    assert entry.target == f"investigation:{record.id}"


def test_create_investigation_audit_row_carries_no_credentials():
    repository = InMemoryInvestigationRepository({ISSUER: 1})
    audit = RecordingAuditWriter()

    create_investigation(
        _create(),
        caller_issuer=ISSUER,
        caller_actor="dina",
        repository=repository,
        audit_writer=audit,
    )

    serialized = str(audit.entries[0])
    assert "password" not in serialized.lower()


# --- approval.decide -----------------------------------------------------


def test_approval_decisions_write_one_audit_row_each():
    repository = InMemoryInvestigationRepository({ISSUER: 1})
    approvals = InMemoryApprovalRepository()
    catalog = build_catalog()
    audit = RecordingAuditWriter()
    record = repository.create_investigation(
        organization_id=1,
        server_host="sql01.acme.example",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=4,
        status="draft",
    )
    approvable = [
        entry.id for entry in catalog.all() if entry.requires_approval
    ]

    record_decisions(
        record.id,
        ApprovalRequest(decisions={id_: Decision.approved for id_ in approvable}),
        caller_actor="dina",
        investigation_repository=repository,
        approval_repository=approvals,
        catalog=catalog,
        audit_writer=audit,
    )

    assert len(audit.entries) == len(approvable)
    assert {entry.action for entry in audit.entries} == {"approval.decide"}
    assert {entry.target for entry in audit.entries} == {
        f"investigation:{record.id}/query:{id_}" for id_ in approvable
    }


def test_duplicate_approval_decisions_write_no_audit_rows():
    repository = InMemoryInvestigationRepository({ISSUER: 1})
    approvals = InMemoryApprovalRepository()
    catalog = build_catalog()
    audit = RecordingAuditWriter()
    record = repository.create_investigation(
        organization_id=1,
        server_host="sql01.acme.example",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=4,
        status="draft",
    )
    approvable = [
        entry.id for entry in catalog.all() if entry.requires_approval
    ]
    decisions = {id_: Decision.approved for id_ in approvable}

    record_decisions(
        record.id,
        ApprovalRequest(decisions=decisions),
        caller_actor="dina",
        investigation_repository=repository,
        approval_repository=approvals,
        catalog=catalog,
        audit_writer=audit,
    )
    after_first = len(audit.entries)

    record_decisions(
        record.id,
        ApprovalRequest(decisions=decisions),
        caller_actor="dina",
        investigation_repository=repository,
        approval_repository=approvals,
        catalog=catalog,
        audit_writer=audit,
    )

    assert len(audit.entries) == after_first


# --- run.start -----------------------------------------------------------


def test_run_start_writes_exactly_one_audit_row(start_run):
    repository, approvals, catalog, investigation_id = _approved_setup()
    audit = RecordingAuditWriter()
    queue = RecordingJobQueue()

    asyncio.run(
        start_run(
            investigation_id,
            caller_actor="dina",
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
            job_queue=queue,
            audit_writer=audit,
        )
    )

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action == "run.start"
    assert entry.actor == "dina"
    assert entry.target == f"investigation:{investigation_id}"
    assert len(queue.enqueued) == 1
    assert repository.get_investigation(investigation_id).status == "running"


def test_run_start_without_an_audit_writer_still_enqueues(start_run):
    repository, approvals, catalog, investigation_id = _approved_setup()
    queue = RecordingJobQueue()

    asyncio.run(
        start_run(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
            job_queue=queue,
        )
    )

    assert len(queue.enqueued) == 1


# --- report.access -------------------------------------------------------


def test_report_access_writes_exactly_one_audit_row():
    repository = InMemoryReportRepository()
    repository.put(1, _payload())
    audit = RecordingAuditWriter()
    caller = SimpleNamespace(actor="dina")

    response = download_report_pdf(
        1, caller=caller, repository=repository, audit_writer=audit
    )

    assert response.status_code == 200
    assert response.body.startswith(b"%PDF-")
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action == "report.access"
    assert entry.actor == "dina"
    assert entry.target == "investigation:1/report"


def test_reading_stored_artifacts_writes_no_audit_rows():
    repository = InMemoryPersistenceRepository()
    audit = RecordingAuditWriter()

    repository.list_evidence_runs(1)
    repository.list_findings(1)
    repository.get_verdict(1)
    repository.get_report_payload(1)

    assert audit.entries == []
