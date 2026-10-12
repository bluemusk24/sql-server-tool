"""Unit tests for the SQL Agent job-failure evidence collector (#15).

Driven with fixture job-history rows (``tests/fixtures/evidence/job_history.json``)
and the #6 :class:`~app.sql.connector.Row` type, so the suite runs with no SQL
Server. The live-server path is the gated ``tests/test_evidence_jobs_db.py``.

Covered: a single failed run, repeated failures (a job failing more than once is
flagged ``repeated``), successful runs that are never evidence, no failures
(zero rows, ``collected``), a permission failure mapping to
``unavailable-permission``, the step-outcome grouping, redaction of a credential
embedded in job step output, timezone-aware ordering, and timestamp boundaries.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.jobs import (
    AREA,
    SOURCE_ID,
    AgentJobsCollector,
    collect_agent_jobs,
)
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.sql.connector import Row

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"


def _load_rows(name: str) -> list[Row]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [Row(tuple(item), tuple(item.values())) for item in data]


def _rows(items: list[dict]) -> list[Row]:
    return [Row(tuple(item), tuple(item.values())) for item in items]


def _registry() -> EvidenceRegistry:
    return EvidenceRegistry(build_catalog())


def _ev(rows=(), **kwargs) -> SourceEvidence:
    return SourceEvidence(source_id=SOURCE_ID, rows=rows, **kwargs)


def test_source_id_and_area():
    assert AgentJobsCollector.source_id == SOURCE_ID
    assert SOURCE_ID == "failed_agent_jobs"
    assert AREA == "agent_jobs"


# --- single failure ---------------------------------------------------------


def test_single_failure_emits_one_row_with_job_outcome_and_message():
    rows = _rows(
        [
            {
                "job_name": "Nightly Load",
                "step_id": 2,
                "step_name": "Load",
                "run_status": 0,
                "run_date": 20261010,
                "run_time": 10030,
                "message": "Cannot insert duplicate key row.",
            }
        ]
    )
    registry = _registry()

    findings = AgentJobsCollector().collect(_ev(rows), registry)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.area == AREA
    assert finding.available is True
    assert finding.status is SourceStatus.collected
    assert finding.timestamp == datetime(2026, 10, 10, 1, 0, 30, tzinfo=UTC)
    assert finding.details["job_name"] == "Nightly Load"
    assert finding.details["run_status"] == 0
    assert finding.details["run_outcome"] == "failed"
    assert finding.details["message"] == "Cannot insert duplicate key row."
    assert finding.details["repeated"] is False
    assert finding.details["failed_runs"] == 1
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


# --- repeated failures ------------------------------------------------------


def test_repeated_job_failures_are_flagged():
    registry = _registry()

    findings = collect_agent_jobs(_ev(_load_rows("job_history.json")), registry)

    repeated = {f.details["job_name"]: f.details["repeated"] for f in findings}
    assert repeated["Repeated ETL"] is True
    assert repeated["Nightly Load"] is False
    assert len(findings) == 3
    assert all(f.area == AREA for f in findings)
    assert "repeated" in registry.get(SOURCE_ID).note


def test_repeated_flag_applies_to_every_failure_of_the_job():
    rows = _rows(
        [
            {"job_name": "J", "run_status": 0, "run_date": 20261010, "run_time": 100,
             "message": "first"},
            {"job_name": "J", "run_status": 0, "run_date": 20261010, "run_time": 200,
             "message": "second"},
        ]
    )

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert len(findings) == 2
    assert all(f.details["repeated"] is True for f in findings)
    assert all(f.details["failed_runs"] == 2 for f in findings)


def test_successful_runs_are_never_evidence():
    rows = _rows(
        [
            {"job_name": "Fresh Stats", "step_id": 1, "run_status": 1,
             "run_date": 20261010, "run_time": 50000, "message": ""},
        ]
    )
    registry = _registry()

    findings = collect_agent_jobs(_ev(rows), registry)

    assert findings == []
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


# --- no failures ------------------------------------------------------------


def test_no_failures_is_zero_rows_but_collected():
    registry = _registry()

    findings = collect_agent_jobs(_ev([]), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.collected
    assert "0 failed job run" in source.note


# --- step outcome grouping --------------------------------------------------


def test_outcome_row_reports_one_run_and_pulls_the_step_message():
    rows = _rows(
        [
            {"job_name": "Job", "step_id": 0, "step_name": "(Job outcome)",
             "run_status": 0, "run_date": 20261010, "run_time": 10000, "message": ""},
            {"job_name": "Job", "step_id": 1, "step_name": "Load",
             "run_status": 0, "run_date": 20261010, "run_time": 10000,
             "message": "The step failed badly."},
        ]
    )

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert len(findings) == 1
    assert findings[0].details["message"] == "The step failed badly."
    assert findings[0].details["failed_step_count"] == 1


# --- redaction and ordering -------------------------------------------------


def test_credential_in_job_step_output_is_redacted():
    findings = collect_agent_jobs(_ev(_load_rows("job_history.json")), _registry())

    rendered = str([finding.model_dump() for finding in findings])
    assert "SuperSecret1" not in rendered
    assert "****" in rendered


def test_findings_are_ordered_by_timestamp_not_row_order():
    rows = _rows(
        [
            {"job_name": "Later", "run_status": 0, "run_date": 20261010,
             "run_time": 40000, "message": "later"},
            {"job_name": "Earlier", "run_status": 0, "run_date": 20261010,
             "run_time": 20000, "message": "earlier"},
        ]
    )

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert [f.details["job_name"] for f in findings] == ["Earlier", "Later"]


# --- timestamp boundaries ---------------------------------------------------


def test_missing_run_time_defaults_to_midnight_utc():
    rows = _rows([{"job_name": "J", "run_status": 0, "run_date": 20261010}])

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert findings[0].timestamp == datetime(2026, 10, 10, tzinfo=UTC)


def test_unparseable_or_missing_date_yields_no_timestamp():
    rows = _rows(
        [
            {"job_name": "NoDate", "run_status": 0, "run_time": 100},
            {"job_name": "BadDate", "run_status": 0, "run_date": 99999999,
             "run_time": 100},
        ]
    )

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert all(f.timestamp is None for f in findings)


def test_missing_run_status_is_treated_as_failed():
    rows = _rows([{"job_name": "J", "run_date": 20261010, "run_time": 1,
                   "message": "x"}])

    findings = collect_agent_jobs(_ev(rows), _registry())

    assert len(findings) == 1
    assert findings[0].details["run_outcome"] == "unknown"


# --- failures ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        (SourceStatus.unavailable_permission, SourceStatus.unavailable_permission),
        (SourceStatus.unavailable_setting, SourceStatus.unavailable_setting),
        (SourceStatus.unavailable_connection, SourceStatus.unavailable_connection),
    ],
)
def test_query_failure_is_never_collected(source_status, expected):
    registry = _registry()

    findings = AgentJobsCollector().collect(
        _ev(
            [],
            status=QueryStatus.error,
            source_status=source_status,
            error="The SELECT permission was denied on the object 'sysjobhistory'",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is expected
    assert expected is not SourceStatus.collected
    assert "denied" in source.note
