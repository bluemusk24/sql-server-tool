"""Unit tests for the database availability evidence collector (#16).

Driven with fixture ``sys.databases`` rows
(``tests/fixtures/evidence/database_states.json``) and the #6
:class:`~app.sql.connector.Row` type, so the suite runs with no SQL Server. The
live-server path is the gated ``tests/test_evidence_availability_db.py``.

Covered: each non-normal state (the six the groomed spec names) maps to one row
carrying the state name, an accessible database produces no rows while the
source stays ``collected``, multiple unavailable databases yield one row each,
``is_in_standby`` is surfaced, #9 scope is respected, and a failed/denied source
is never reported as ``collected``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.availability import (
    AREA,
    NON_NORMAL_STATES,
    SOURCE_ID,
    AvailabilityCollector,
    collect_availability,
    normalize_state,
)
from app.evidence.collectors import SourceEvidence
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.sql.connector import Row

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"


def _load_rows(name: str) -> list[Row]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [Row(tuple(item), tuple(item.values())) for item in data]


def _rows_from(items: list[dict]) -> list[Row]:
    return [Row(tuple(item), tuple(item.values())) for item in items]


def _registry() -> EvidenceRegistry:
    return EvidenceRegistry(build_catalog())


def _ev(rows=(), **kwargs) -> SourceEvidence:
    return SourceEvidence(source_id=SOURCE_ID, rows=rows, **kwargs)


def _state_row(name: str, state_desc: str, state: int, **extra) -> dict:
    values = {
        "database_name": name,
        "state": state,
        "state_desc": state_desc,
        "user_access_desc": "MULTI_USER",
        "is_in_standby": 0,
        "recovery_model_desc": "FULL",
    }
    values.update(extra)
    return values


# --- state mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("state_desc", "state", "expected"),
    [
        ("OFFLINE", 6, "offline"),
        ("SUSPECT", 4, "suspect"),
        ("RECOVERING", 2, "recovering"),
        ("IN RECOVERY", 2, "in_recovery"),
        ("RESTORING", 1, "restoring"),
        ("RECOVERY_PENDING", 3, "recovery_pending"),
    ],
)
def test_each_unavailable_state_maps_to_a_row_with_the_state_name(
    state_desc, state, expected
):
    registry = _registry()
    rows = _rows_from([_state_row("AppDb", state_desc, state)])

    findings = AvailabilityCollector().collect(_ev(rows), registry)

    assert len(findings) == 1
    finding = findings[0]
    assert expected in NON_NORMAL_STATES
    assert finding.area == AREA
    assert finding.details["state"] == expected
    assert expected in finding.description
    assert finding.status is SourceStatus.collected
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_numeric_state_code_is_used_when_state_desc_is_missing():
    rows = _rows_from([{"database_name": "AppDb", "state": 6}])
    registry = _registry()

    findings = collect_availability(_ev(rows), registry)

    assert len(findings) == 1
    assert findings[0].details["state"] == "offline"


def test_accessible_database_produces_no_rows_and_source_stays_collected():
    rows = _rows_from([_state_row("Healthy", "ONLINE", 0)])
    registry = _registry()

    findings = collect_availability(_ev(rows), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.collected
    assert "0 database" in source.note


def test_online_database_in_standby_is_surfaced_as_standby():
    rows = _rows_from([_state_row("Secondary", "ONLINE", 0, is_in_standby=1)])
    registry = _registry()

    findings = collect_availability(_ev(rows), registry)

    assert len(findings) == 1
    assert findings[0].details["state"] == "standby"
    assert findings[0].details["is_in_standby"] == 1


def test_multiple_unavailable_databases_yield_one_row_each():
    registry = _registry()

    findings = collect_availability(_ev(_load_rows("database_states.json")), registry)

    by_name = {finding.details["database_name"]: finding for finding in findings}
    assert set(by_name) == {
        "Archived",
        "Broken",
        "Recovering",
        "Restoring",
        "Pending",
        "Standby",
    }
    assert by_name["Archived"].details["state"] == "offline"
    assert by_name["Broken"].details["state"] == "suspect"
    assert "Sales" not in by_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OFFLINE", "offline"),
        ("Recovery_Pending", "recovery_pending"),
        ("IN RECOVERY", "in_recovery"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_state(raw, expected):
    assert normalize_state(raw) == expected


# --- scope ------------------------------------------------------------------


def test_scope_keeps_only_named_databases():
    rows = _load_rows("database_states.json")
    registry = _registry()

    findings = AvailabilityCollector(databases=["Broken", "Sales"]).collect(
        _ev(rows), registry
    )

    assert len(findings) == 1
    assert findings[0].details["database_name"] == "Broken"
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_scope_is_case_insensitive():
    rows = _rows_from([_state_row("AppDb", "OFFLINE", 6)])

    findings = AvailabilityCollector(databases=["appdb"]).collect(_ev(rows), _registry())

    assert len(findings) == 1


def test_out_of_scope_unavailable_databases_produce_no_rows():
    rows = _rows_from([_state_row("OtherDb", "OFFLINE", 6)])
    registry = _registry()

    findings = collect_availability(_ev(rows), registry, databases=["AppDb"])

    assert findings == []
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


# --- empty and failures -----------------------------------------------------


def test_empty_result_is_zero_rows_but_collected():
    registry = _registry()

    findings = collect_availability(_ev([]), registry)

    assert findings == []
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_missing_permission_is_unavailable_permission_with_zero_rows():
    registry = _registry()

    findings = AvailabilityCollector().collect(
        _ev(
            [],
            status=QueryStatus.error,
            source_status=SourceStatus.unavailable_permission,
            error="The SELECT permission was denied on the object 'sys.databases'",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.unavailable_permission
    assert "denied" in source.note


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        (SourceStatus.unavailable_connection, SourceStatus.unavailable_connection),
        (SourceStatus.unavailable_setting, SourceStatus.unavailable_setting),
        (SourceStatus.collected, SourceStatus.unavailable_setting),
    ],
)
def test_failed_query_is_never_collected(source_status, expected):
    registry = _registry()

    findings = AvailabilityCollector().collect(
        _ev(
            [],
            status=QueryStatus.error,
            source_status=source_status,
            error="source query failed",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is expected
    assert source.status is not SourceStatus.collected
