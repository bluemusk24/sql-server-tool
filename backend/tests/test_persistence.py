"""Unit tests for #21 persistence and 7-day retention.

Everything runs with no SQL Server and no network: the in-memory
persistence double models the five #21 tables, so the round-trip,
retention and redaction criteria are exercised directly. The
SQLAlchemy implementations are covered by the ``RUN_DB_TESTS=1``
integration tests.

Covered: round-tripping an evidence run, finding, verdict and
report payload for one investigation; the fixed 7-day retention
window with an injectable clock (records aged 6 and 7 days are
retained, records strictly older than 7 days are deleted, across
all five tables); and the #5 redaction filter running before
anything is stored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.ai.schemas import Cause, ConfidenceLabel, Verdict
from app.evidence.models import QueryEvidence, QueryStatus
from app.investigations.repository import AuditEntry
from app.persistence import (
    RETENTION_DAYS,
    RETENTION_WINDOW,
    InMemoryPersistenceRepository,
    sanitize_json,
)
from app.report.schemas import ReportPayload
from app.rules.signals import Severity, Signal

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _evidence() -> QueryEvidence:
    return QueryEvidence(
        query_id="blocking_current_chains",
        database="master",
        status=QueryStatus.ok,
        row_count=1,
        duration_ms=12.5,
        rows=[{"session_id": 60, "blocking_session_id": 57}],
    )


def _signal() -> Signal:
    return Signal(
        area="blocking",
        severity=Severity.high,
        description="Session 60 is blocked by session 57.",
        evidence_ids=["ev-blocking-1"],
    )


def _verdict(*, percent: int = 82) -> Verdict:
    from app.ai.schemas import confidence_label

    return Verdict(
        primary_cause=Cause(
            area="blocking",
            description="Session 60 is blocked by session 57.",
            confidence_percent=percent,
            confidence_label=confidence_label(percent),
        ),
        alternatives=[],
        confidence_percent=percent,
        confidence_label=confidence_label(percent),
        severity=Severity.high,
        rationale="One high-severity blocking signal.",
        inconclusive=False,
    )


def _payload() -> ReportPayload:
    return ReportPayload(
        investigation_id=1,
        organization="Acme Corp",
        server_host="sql01.acme.example",
        server_port=1433,
        database_scope="instance",
        databases=[],
        lookback_hours=4,
        investigation_time=NOW,
        data_sources=[],
        findings_by_area=[],
        primary_cause=_verdict().primary_cause,
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


def _audit() -> AuditEntry:
    return AuditEntry(
        organization_id=0,
        actor="dina",
        action="investigation.create",
        target="investigation:1",
    )


# --- round-trip ----------------------------------------------------------


def test_artifacts_round_trip_for_one_investigation():
    repository = InMemoryPersistenceRepository()

    repository.record_evidence_run(1, _evidence())
    repository.record_finding(1, _signal())
    repository.record_verdict(1, _verdict())
    repository.store_report_payload(1, _payload())

    runs = repository.list_evidence_runs(1)
    assert len(runs) == 1
    assert runs[0].source == "blocking_current_chains"
    assert runs[0].status == "ok"
    assert runs[0].results == [{"session_id": 60, "blocking_session_id": 57}]
    assert runs[0].duration == 12.5
    assert runs[0].error is None

    findings = repository.list_findings(1)
    assert len(findings) == 1
    assert findings[0].area == "blocking"
    assert findings[0].severity == "high"
    assert findings[0].description == "Session 60 is blocked by session 57."
    assert findings[0].evidence_refs == ["ev-blocking-1"]

    assert repository.get_verdict(1) == _verdict()
    assert repository.get_report_payload(1) == _payload()


def test_lists_are_scoped_to_the_investigation():
    repository = InMemoryPersistenceRepository()
    repository.record_evidence_run(1, _evidence())
    repository.record_finding(1, _signal())
    repository.record_verdict(1, _verdict())
    repository.store_report_payload(1, _payload())

    assert repository.list_evidence_runs(2) == []
    assert repository.list_findings(2) == []
    assert repository.get_verdict(2) is None
    assert repository.get_report_payload(2) is None


def test_newest_verdict_wins():
    repository = InMemoryPersistenceRepository()
    repository.record_verdict(1, _verdict(percent=82))
    repository.record_verdict(1, _verdict(percent=40))

    verdict = repository.get_verdict(1)

    assert verdict is not None
    assert verdict.confidence_percent == 40


def test_report_payload_is_replaced_per_investigation():
    repository = InMemoryPersistenceRepository()
    repository.store_report_payload(1, _payload())
    newer = _payload().model_copy(update={"organization": "Other Org"})

    repository.store_report_payload(1, newer)

    stored = repository.get_report_payload(1)
    assert stored is not None
    assert stored.organization == "Other Org"


# --- retention -----------------------------------------------------------


def test_retention_deletes_only_rows_strictly_older_than_seven_days():
    clock = {"now": NOW}
    repository = InMemoryPersistenceRepository(clock=lambda: clock["now"])

    for age in (RETENTION_DAYS + 1, RETENTION_DAYS, RETENTION_DAYS - 1):
        clock["now"] = NOW - timedelta(days=age)
        repository.record_evidence_run(1, _evidence())
        repository.record_finding(1, _signal())
        repository.record_verdict(1, _verdict())
        repository.record_audit(_audit())
    # report_payload holds one row per investigation (unique constraint).
    clock["now"] = NOW - timedelta(days=RETENTION_DAYS + 1)
    repository.store_report_payload(1, _payload())
    clock["now"] = NOW - timedelta(days=RETENTION_DAYS - 1)
    repository.store_report_payload(2, _payload())

    report = repository.cleanup(now=NOW)

    assert report.deleted == {
        "evidence_run": 1,
        "finding": 1,
        "verdict": 1,
        "report_payload": 1,
        "audit": 1,
    }
    assert report.total == 5
    assert len(repository.list_evidence_runs(1)) == 2
    assert len(repository.list_findings(1)) == 2
    assert repository.get_verdict(1) is not None
    assert repository.get_report_payload(1) is None
    assert repository.get_report_payload(2) is not None


def test_retention_keeps_a_record_at_exactly_the_seven_day_boundary():
    clock = {"now": NOW}
    repository = InMemoryPersistenceRepository(clock=lambda: clock["now"])
    clock["now"] = NOW - RETENTION_WINDOW
    repository.record_evidence_run(1, _evidence())

    report = repository.cleanup(now=NOW)

    assert report.deleted["evidence_run"] == 0
    assert len(repository.list_evidence_runs(1)) == 1


def test_retention_window_is_a_fixed_seven_days():
    assert RETENTION_DAYS == 7
    assert RETENTION_WINDOW == timedelta(days=7)


# --- redaction -----------------------------------------------------------


def test_sanitize_json_masks_secret_keys_and_secret_shaped_text():
    value = {
        "username": "sa",
        "password": "hunter2",
        "connection": "server=sql01;password=hunter2;",
        "note": "nothing sensitive",
    }

    sanitized = sanitize_json(value)

    assert sanitized["username"] == "sa"
    assert sanitized["password"] == "****"
    assert "hunter2" not in json.dumps(sanitized)
    assert sanitized["note"] == "nothing sensitive"


def test_stored_payload_with_a_password_key_is_redacted_before_storage():
    repository = InMemoryPersistenceRepository()
    dirty = _payload().model_dump(mode="json")
    dirty["connection"] = {"username": "sa", "password": "hunter2"}

    repository.store_report_payload(1, dirty)
    stored = repository.get_stored_payload(1)

    assert stored is not None
    assert stored != dirty
    assert stored["connection"]["password"] == "****"
    assert stored["connection"]["username"] == "sa"
    assert "hunter2" not in json.dumps(stored)


def test_evidence_errors_are_redacted_before_storage():
    repository = InMemoryPersistenceRepository()
    evidence = QueryEvidence(
        query_id="blocking_current_chains",
        database="master",
        status=QueryStatus.error,
        error="login failed for user 'sa' with password=hunter2",
    )

    repository.record_evidence_run(1, evidence)

    stored = repository.list_evidence_runs(1)[0]
    assert stored.error is not None
    assert "hunter2" not in stored.error
    assert "password=****" in stored.error
