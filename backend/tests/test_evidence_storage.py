"""Unit tests for the storage-pressure evidence collector (#16).

Driven with fixture ``sys.master_files``/``sys.dm_os_volume_stats`` rows
(``tests/fixtures/evidence/volume_space.json``) and the #6
:class:`~app.sql.connector.Row` type, so the suite runs with no SQL Server. The
live-server path is the gated ``tests/test_evidence_availability_db.py``.

Covered: the 10% threshold boundary (exactly 10.0% free is *not* flagged, 9.9%
is), one row per low-space file, the free percentage carried on the row, a
``free_percent``-only row without byte counters, the ``StorageCollector``
extension point, the per-scope row cap, #9 scope, the empty source, and
permission/timeout failures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.evidence.storage import (
    AREA,
    DEFAULT_INDICATORS,
    LOW_FREE_SPACE_RATIO,
    MAX_STORAGE_ROWS,
    SOURCE_ID,
    DatabaseFiles,
    DatabaseVolumeCollector,
    LowFreeSpaceCollector,
    StorageCollector,
    StorageIndicator,
    collect_storage,
    free_ratio,
)
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


def _file_row(name: str, path: str, available: float, total: float, **extra) -> dict:
    values = {
        "database_name": name,
        "physical_name": path,
        "logical_volume_name": path[:1],
        "total_bytes": total,
        "available_bytes": available,
    }
    values.update(extra)
    return values


# --- threshold and boundary -------------------------------------------------


def test_low_free_space_ratio_constant_is_ten_percent():
    assert LOW_FREE_SPACE_RATIO == 0.10


def test_free_space_boundary_exactly_ten_percent_is_not_flagged_but_9_9_percent_is():
    rows = _rows_from(
        [
            _file_row("Boundary", "B:\\data\\exact.mdf", 100, 1000),
            _file_row("Boundary", "B:\\data\\just_under.mdf", 99, 1000),
        ]
    )
    registry = _registry()

    findings = collect_storage(_ev(rows), registry)

    assert len(findings) == 1
    assert findings[0].details["physical_name"] == "B:\\data\\just_under.mdf"
    assert findings[0].details["free_percent"] == 9.9
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_healthy_files_at_or_above_threshold_produce_no_rows_and_stay_collected():
    rows = _rows_from(
        [
            _file_row("Healthy", "H:\\data\\big.mdf", 500, 1000),
            _file_row("Healthy", "H:\\data\\exact.mdf", 100, 1000),
        ]
    )
    registry = _registry()

    findings = collect_storage(_ev(rows), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.collected
    assert "0 storage pressure" in source.note


def test_file_row_includes_the_free_percentage():
    rows = _rows_from([_file_row("AppDb", "A:\\data\\app.mdf", 42, 840)])
    registry = _registry()

    findings = collect_storage(_ev(rows), registry)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.area == AREA
    assert finding.details["free_percent"] == 5.0
    assert finding.details["kind"] == "low_free_space"
    assert "5.0%" in finding.description
    assert finding.status is SourceStatus.collected


def test_free_percent_column_is_used_when_byte_counters_are_absent():
    rows = _rows_from(
        [
            {
                "database_name": "Legacy",
                "physical_name": "L:\\data\\legacy.mdf",
                "free_percent": 9.5,
            }
        ]
    )
    registry = _registry()

    findings = collect_storage(_ev(rows), registry)

    assert len(findings) == 1
    assert findings[0].details["free_percent"] == 9.5


def test_multiple_low_space_files_in_one_database_yield_one_row_per_file():
    rows = _rows_from(
        [
            _file_row("Hot", "H:\\data\\hot.mdf", 10, 1000),
            _file_row("Hot", "H:\\log\\hot_log.ldf", 20, 1000),
            _file_row("Hot", "H:\\data\\spare.mdf", 300, 1000),
        ]
    )
    registry = _registry()

    findings = collect_storage(_ev(rows), registry)

    assert len(findings) == 2
    assert [f.details["free_percent"] for f in findings] == [1.0, 2.0]
    assert all(f.details["database_name"] == "Hot" for f in findings)


@pytest.mark.parametrize(
    "row",
    [
        {"database_name": "X", "physical_name": "x", "total_bytes": 0,
         "available_bytes": 0},
        {"database_name": "X", "physical_name": "x"},
        {"database_name": "X", "physical_name": "x", "total_bytes": 100,
         "available_bytes": None},
    ],
)
def test_unavailable_free_space_yields_no_row(row):
    assert free_ratio(Row(tuple(row), tuple(row.values()))) is None


# --- fixture ----------------------------------------------------------------


def test_fixture_only_flags_files_below_threshold():
    registry = _registry()

    findings = collect_storage(_ev(_load_rows("volume_space.json")), registry)

    flagged = {f.details["physical_name"] for f in findings}
    assert flagged == {
        "S:\\log\\sales_log.ldf",
        "R:\\log\\reporting_log.ldf",
        "L:\\data\\legacy.mdf",
    }
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


# --- extension point --------------------------------------------------------


class _AlwaysIndicator:
    """Toy indicator proving a new one plugs in without touching the driver."""

    kind = "toy"

    def collect(self, database: DatabaseFiles) -> list[StorageIndicator]:
        return [
            StorageIndicator(
                database=database.name,
                kind=self.kind,
                description=f"toy finding for {database.name}",
            )
        ]


def test_additional_indicator_plugs_in_without_changing_the_caller():
    rows = _rows_from([_file_row("AppDb", "A:\\data\\app.mdf", 500, 1000)])
    registry = _registry()

    findings = DatabaseVolumeCollector(
        indicators=(*DEFAULT_INDICATORS, _AlwaysIndicator())
    ).collect(_ev(rows), registry)

    kinds = {f.details["kind"] for f in findings}
    assert kinds == {"toy"}
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_low_free_space_collector_is_a_storage_collector():
    assert isinstance(LowFreeSpaceCollector(), StorageCollector)


# --- cap, scope, empty, failures -------------------------------------------


def test_results_are_capped_at_the_documented_constant():
    rows = _rows_from(
        [_file_row("Hot", f"H:\\data\\f{index}.mdf", 10, 1000) for index in range(5)]
    )
    registry = _registry()

    findings = DatabaseVolumeCollector(max_rows=2).collect(_ev(rows), registry)

    assert len(findings) == 2
    assert MAX_STORAGE_ROWS == 500


def test_scope_ignores_out_of_scope_databases():
    rows = _rows_from(
        [
            _file_row("InScope", "A:\\data\\a.mdf", 10, 1000),
            _file_row("OutOfScope", "B:\\data\\b.mdf", 10, 1000),
        ]
    )
    registry = _registry()

    findings = DatabaseVolumeCollector(databases=["InScope"]).collect(
        _ev(rows), registry
    )

    assert [f.details["database_name"] for f in findings] == ["InScope"]


def test_out_of_scope_only_rows_yield_no_findings_but_stay_collected():
    rows = _rows_from([_file_row("Other", "B:\\data\\b.mdf", 10, 1000)])
    registry = _registry()

    findings = collect_storage(_ev(rows), registry, databases=["InScope"])

    assert findings == []
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_empty_result_is_not_applicable():
    registry = _registry()

    findings = collect_storage(_ev([]), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.not_applicable
    assert "no volume statistics" in source.note


def test_missing_permission_is_unavailable_permission_with_zero_rows():
    registry = _registry()

    findings = DatabaseVolumeCollector().collect(
        _ev(
            [],
            status=QueryStatus.error,
            source_status=SourceStatus.unavailable_permission,
            error="The SELECT permission was denied on the object 'dm_os_volume_stats'",
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

    findings = DatabaseVolumeCollector().collect(
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
