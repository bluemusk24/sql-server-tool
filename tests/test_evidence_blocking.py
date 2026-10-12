"""Unit tests for the blocking and deadlock evidence collectors (#13).

Everything is driven with fixture result sets (plain data mimicking DMV rows)
and the #6 :class:`~app.sql.connector.Row` type, so the suite runs with no SQL
Server. Fixtures live in ``tests/fixtures/evidence/`` so #14-#16 follow the same
pattern. The live-server path is the gated ``tests/test_evidence_blocking_db.py``.

Covered: a multi-session blocking chain (root blocker and per-session rows), no
blocking, the historical-blocking gap marker, permission/timeout failures, two
deadlock graphs parsed from retained XML (victims and participants), a ring with
no deadlocks, no retained ring buffer, and null/missing columns.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.blocking import (
    CURRENT_SOURCE_ID,
    HISTORICAL_SOURCE_ID,
    NO_HISTORY_NOTE,
    CurrentBlockingCollector,
    HistoricalBlockingCollector,
    collect_blocking,
)
from app.evidence.collectors import SourceEvidence
from app.evidence.deadlocks import (
    SOURCE_ID as DEADLOCK_SOURCE_ID,
)
from app.evidence.deadlocks import (
    DeadlockCollector,
    collect_deadlocks,
    parse_deadlock_graphs,
)
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.sql.connector import Row

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"


def _load_rows(name: str) -> list[Row]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [Row(tuple(item), tuple(item.values())) for item in data]


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _registry() -> EvidenceRegistry:
    return EvidenceRegistry(build_catalog())


def _ev(source_id: str, rows=(), **kwargs) -> SourceEvidence:
    return SourceEvidence(source_id=source_id, rows=rows, **kwargs)


# --- EvidenceRow shape ------------------------------------------------------


def test_evidence_row_carries_area_timestamp_description_and_source_status():
    row = EvidenceRow(area="blocking", description="Session 1 is blocked.")

    assert row.area == "blocking"
    assert row.timestamp is None
    assert row.description == "Session 1 is blocked."
    assert row.available is True
    assert row.status is SourceStatus.collected
    assert row.details == {}


# --- current blocking -------------------------------------------------------


def test_blocking_chain_emits_one_row_per_blocked_session_with_root_blocker():
    rows = _load_rows("blocking_chain.json")
    registry = _registry()

    findings = CurrentBlockingCollector().collect(
        _ev(CURRENT_SOURCE_ID, rows), registry
    )

    assert len(findings) == 3
    by_blocked = {f.details["blocked_session_id"]: f for f in findings}
    assert by_blocked[57].details["blocking_session_id"] == 52
    assert by_blocked[60].details["blocking_session_id"] == 57
    assert by_blocked[55].details["blocking_session_id"] == 57
    assert all(f.details["root_blocker_session_id"] == 52 for f in findings)
    assert by_blocked[60].details["duration_ms"] == 8200
    assert all(f.area == "blocking" for f in findings)
    assert all(f.available is True for f in findings)
    assert all(f.timestamp is None for f in findings)
    assert registry.get(CURRENT_SOURCE_ID).status is SourceStatus.collected


def test_no_blocking_is_zero_rows_and_still_collected():
    registry = _registry()

    findings = CurrentBlockingCollector().collect(
        _ev(CURRENT_SOURCE_ID, []), registry
    )

    assert findings == []
    assert registry.get(CURRENT_SOURCE_ID).status is SourceStatus.collected


def test_null_and_missing_columns_are_handled():
    rows = [Row(("session_id", "blocking_session_id"), (10, 5))]
    registry = _registry()

    findings = CurrentBlockingCollector().collect(
        _ev(CURRENT_SOURCE_ID, rows), registry
    )

    assert len(findings) == 1
    assert findings[0].details["duration_ms"] is None
    assert findings[0].details["wait_type"] is None
    assert findings[0].details["root_blocker_session_id"] == 5
    assert "unknown duration" in findings[0].description


def test_duration_boundaries_are_recorded_verbatim():
    rows = [
        Row(("session_id", "blocking_session_id", "wait_time"), (10, 5, 0)),
        Row(("session_id", "blocking_session_id", "wait_time"), (11, 5, 86400000)),
    ]
    registry = _registry()

    findings = CurrentBlockingCollector().collect(
        _ev(CURRENT_SOURCE_ID, rows), registry
    )

    durations = {f.details["blocked_session_id"]: f.details["duration_ms"] for f in findings}
    assert durations == {10: 0, 11: 86400000}


# --- historical blocking gap -------------------------------------------------


def test_historical_blocking_gap_marker_is_emitted():
    registry = _registry()

    findings = collect_blocking(_ev(CURRENT_SOURCE_ID, []), registry)

    gap = [f for f in findings if not f.available]
    assert len(gap) == 1
    assert gap[0].status is SourceStatus.not_applicable
    assert gap[0].details["note"] == NO_HISTORY_NOTE
    source = registry.get(HISTORICAL_SOURCE_ID)
    assert source is not None
    assert source.status is SourceStatus.not_applicable
    assert source.note == NO_HISTORY_NOTE


def test_retained_historical_blocking_emits_rows():
    rows = [Row(("session_id", "blocking_session_id", "wait_time"), (5, 4, 100))]
    registry = _registry()

    findings = HistoricalBlockingCollector().collect(
        _ev(HISTORICAL_SOURCE_ID, rows), registry
    )

    assert len(findings) == 1
    assert findings[0].available is True
    assert findings[0].details["blocked_session_id"] == 5
    assert registry.get(HISTORICAL_SOURCE_ID).status is SourceStatus.collected


# --- failures ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        (SourceStatus.unavailable_permission, SourceStatus.unavailable_permission),
        (SourceStatus.unavailable_setting, SourceStatus.unavailable_setting),
        (SourceStatus.not_applicable, SourceStatus.unavailable_setting),
    ],
)
def test_blocking_query_failure_never_reports_collected(source_status, expected):
    registry = _registry()

    findings = CurrentBlockingCollector().collect(
        _ev(
            CURRENT_SOURCE_ID,
            [],
            status=QueryStatus.error,
            source_status=source_status,
            error="The SELECT permission was denied on the object 'sys.dm_exec_requests'",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(CURRENT_SOURCE_ID)
    assert source.status is expected
    assert expected is not SourceStatus.collected
    assert "denied" in source.note


def test_deadlock_query_failure_marks_unavailable_permission():
    registry = _registry()

    findings = DeadlockCollector().collect(
        _ev(
            DEADLOCK_SOURCE_ID,
            [],
            status=QueryStatus.error,
            source_status=SourceStatus.unavailable_permission,
            error="VIEW SERVER STATE permission was denied",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(DEADLOCK_SOURCE_ID)
    assert source.status is SourceStatus.unavailable_permission
    assert "denied" in source.note


# --- deadlocks --------------------------------------------------------------


def test_two_deadlock_graphs_yield_two_rows_with_victims():
    xml = _fixture_text("system_health_deadlocks.xml")
    ring_timestamp = datetime.fromisoformat("2026-10-10T00:00:00")
    rows = [Row(("ring_timestamp", "ring_data"), (ring_timestamp, xml))]
    registry = _registry()

    findings = DeadlockCollector().collect(_ev(DEADLOCK_SOURCE_ID, rows), registry)

    assert len(findings) == 2
    assert [f.details["victim"] for f in findings] == [61, 88]
    assert findings[0].timestamp == datetime.fromisoformat("2026-10-10T14:22:11.123")
    assert findings[1].timestamp == datetime.fromisoformat("2026-10-10T14:25:44.456")
    assert len(findings[1].details["participants"]) == 3
    assert all(f.area == "deadlocks" for f in findings)
    assert all(f.status is SourceStatus.collected for f in findings)
    assert registry.get(DEADLOCK_SOURCE_ID).status is SourceStatus.collected


def test_ring_with_no_deadlock_graphs_is_collected_with_zero_rows():
    xml = _fixture_text("system_health_no_deadlocks.xml")
    rows = [Row(("ring_timestamp", "ring_data"), (None, xml))]
    registry = _registry()

    findings = collect_deadlocks(_ev(DEADLOCK_SOURCE_ID, rows), registry)

    assert findings == []
    assert registry.get(DEADLOCK_SOURCE_ID).status is SourceStatus.collected


def test_no_retained_ring_buffer_is_not_applicable():
    registry = _registry()

    findings = DeadlockCollector().collect(_ev(DEADLOCK_SOURCE_ID, []), registry)

    assert findings == []
    assert registry.get(DEADLOCK_SOURCE_ID).status is SourceStatus.not_applicable


@pytest.mark.parametrize("payload", [None, "", "   ", "not xml at all", "<root/>"])
def test_parse_deadlock_graphs_handles_empty_malformed_and_non_deadlock(payload):
    assert parse_deadlock_graphs(payload) == []


def test_deadlock_timestamp_falls_back_to_ring_timestamp():
    xml = "<deadlock><victim-list><victimProcess id='p1'/></victim-list></deadlock>"
    fallback = datetime.fromisoformat("2026-01-02T03:04:05")

    findings = parse_deadlock_graphs(xml, fallback)

    assert len(findings) == 1
    assert findings[0].timestamp == fallback
    assert findings[0].details["victim"] == "p1"
