"""Unit tests for the SQL Server error-log evidence collector (#15).

Driven with fixture error-log rows (``tests/fixtures/evidence/error_log.json``)
and the #6 :class:`~app.sql.connector.Row` type, so the suite runs with no SQL
Server. The live-server path is the gated ``tests/test_evidence_jobs_db.py``.

Covered: one row per entry, the severity mapping (``>= 17`` high, ``11-16``
medium, ``0-10`` low, with ``>= 17`` flagged critical), a severity column taking
precedence over the text, redaction of a connection-string credential,
no entries (zero rows, ``collected``), a permission failure mapping to
``unavailable-permission``, timezone-aware timestamp parsing, and ordering by
timestamp.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.errors import (
    AREA,
    SOURCE_ID,
    ErrorLogCollector,
    collect_errors,
    parse_severity,
    severity_label,
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
    assert ErrorLogCollector.source_id == SOURCE_ID
    assert SOURCE_ID == "sql_server_error_log"
    assert AREA == "errors"


# --- one row per entry ------------------------------------------------------


def test_one_row_per_error_log_entry():
    registry = _registry()

    findings = collect_errors(_ev(_load_rows("error_log.json")), registry)

    assert len(findings) == 4
    assert all(f.area == AREA for f in findings)
    assert all(f.available is True for f in findings)
    assert all(f.status is SourceStatus.collected for f in findings)
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


# --- severity mapping -------------------------------------------------------


def test_severity_is_mapped_from_the_message_text():
    findings = collect_errors(_ev(_load_rows("error_log.json")), _registry())

    by_process = {f.details["process_info"]: f.details for f in findings}
    assert by_process["spid51"]["severity"] == "medium"
    assert by_process["spid51"]["severity_code"] == 14
    assert by_process["spid51"]["critical"] is False
    assert by_process["spid60"]["severity"] == "high"
    assert by_process["spid60"]["severity_code"] == 24
    assert by_process["spid60"]["critical"] is True
    assert by_process["spid22"]["severity"] == "low"
    assert by_process["spid23"]["severity"] == "low"


def test_severity_17_is_high_and_critical():
    rows = _rows(
        [
            {
                "log_date": "2026-10-10T00:00:00",
                "error_text": "Error: 9002, Severity: 17, State: 1. The log is full.",
            }
        ]
    )

    finding = collect_errors(_ev(rows), _registry())[0]

    assert finding.details["severity"] == "high"
    assert finding.details["critical"] is True
    assert "Critical" in finding.description


def test_severity_column_takes_precedence_over_text():
    rows = _rows(
        [
            {
                "log_date": "2026-10-10T00:00:00",
                "severity": 24,
                "error_text": "Error: 1, Severity: 10, State: 1.",
            }
        ]
    )

    finding = collect_errors(_ev(rows), _registry())[0]

    assert finding.details["severity_code"] == 24
    assert finding.details["severity"] == "high"


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (24, "high"),
        (17, "high"),
        (16, "medium"),
        (11, "medium"),
        (10, "low"),
        (0, "low"),
        (None, "low"),
    ],
)
def test_severity_label_boundaries(severity, expected):
    assert severity_label(severity) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Error: 18456, Severity: 14, State: 1.", 14),
        ("Severity: 20", 20),
        ("Level 16: something failed", 16),
        ("no severity marker here", None),
        ("", None),
    ],
)
def test_parse_severity_patterns(text, expected):
    assert parse_severity(text) == expected


# --- redaction --------------------------------------------------------------


def test_connection_string_secret_in_error_text_is_redacted():
    findings = collect_errors(_ev(_load_rows("error_log.json")), _registry())

    rendered = str([finding.model_dump() for finding in findings])
    assert "Sup3rSecret" not in rendered
    assert "Password=****" in rendered


# --- timestamps and ordering ------------------------------------------------


def test_naive_log_date_is_normalised_to_utc():
    rows = _rows(
        [
            {
                "log_date": "2026-10-10T02:15:00",
                "error_text": "Error: 1, Severity: 14, State: 1.",
            }
        ]
    )

    finding = collect_errors(_ev(rows), _registry())[0]

    assert finding.timestamp == datetime(2026, 10, 10, 2, 15, tzinfo=UTC)
    assert finding.timestamp.tzinfo is UTC


def test_unparseable_log_date_yields_no_timestamp():
    rows = _rows([{"log_date": "not-a-date", "error_text": "boom"}])

    finding = collect_errors(_ev(rows), _registry())[0]

    assert finding.timestamp is None


def test_findings_are_ordered_by_timestamp_not_row_order():
    rows = _rows(
        [
            {"log_date": "2026-10-10T05:00:00", "error_text": "later"},
            {"log_date": "2026-10-10T01:00:00", "error_text": "earlier"},
        ]
    )

    findings = collect_errors(_ev(rows), _registry())

    assert [f.details["error_text"] for f in findings] == ["earlier", "later"]


# --- no entries and failures ------------------------------------------------


def test_no_entries_is_zero_rows_but_collected():
    registry = _registry()

    findings = collect_errors(_ev([]), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.collected
    assert "0 error log entr" in source.note


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

    findings = ErrorLogCollector().collect(
        _ev(
            [],
            status=QueryStatus.error,
            source_status=source_status,
            error="VIEW SERVER STATE permission was denied",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is expected
    assert expected is not SourceStatus.collected
    assert "denied" in source.note
