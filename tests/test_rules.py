"""Per-rule trigger and non-trigger tests for the #17 rules engine.

Each rule module is a pure function over indexed evidence rows. These tests
drive every rule directly with plain :class:`~app.evidence.models.EvidenceRow`
fixtures (no SQL, no execution result) and assert the exact severity / threshold
behaviour the groomed spec names. The thresholds themselves are imported from
:mod:`app.rules.thresholds`, the single source of truth, so a threshold change
cannot silently diverge from its test.
"""

from __future__ import annotations

from app.diagnostics.catalog import ISSUE_AREAS as CATALOG_ISSUE_AREAS
from app.evidence.models import EvidenceRow
from app.rules import (
    availability,
    blocking,
    deadlocks,
    errors,
    jobs,
    queries,
    storage,
)
from app.rules.engine import AREA_ORDER, evidence_id
from app.rules.signals import IndexedEvidence, Severity
from app.rules.thresholds import (
    BLOCKING_HIGH_MS,
    BLOCKING_MEDIUM_MS,
    ERROR_SEVERITY_HIGH,
    ERROR_SEVERITY_MEDIUM,
    QUERY_HIGH_METRIC_US,
    QUERY_MEDIUM_METRIC_US,
    STORAGE_FREE_HIGH_RATIO,
    STORAGE_FREE_LOW_RATIO,
)


def _indexed(rows: list[EvidenceRow]) -> list[IndexedEvidence]:
    return [IndexedEvidence(evidence_id(row), row) for row in rows]


def _row(area: str, details: dict | None = None, **kwargs) -> EvidenceRow:
    values: dict = {"area": area, "description": f"{area} evidence"}
    if details is not None:
        values["details"] = details
    values.update(kwargs)
    return EvidenceRow(**values)


# --- blocking ---------------------------------------------------------------


def test_blocking_at_high_threshold_is_high():
    row = _row(
        "blocking",
        {"duration_ms": BLOCKING_HIGH_MS, "blocked_session_id": 60,
         "blocking_session_id": 57},
    )

    signals = blocking.evaluate(_indexed([row]))

    assert len(signals) == 1
    assert signals[0].severity is Severity.high
    assert signals[0].area == blocking.AREA
    assert signals[0].evidence_ids == [evidence_id(row)]


def test_blocking_between_thresholds_is_medium():
    row = _row(
        "blocking",
        {"duration_ms": BLOCKING_MEDIUM_MS, "blocked_session_id": 60,
         "blocking_session_id": 57},
    )

    signals = blocking.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.medium


def test_blocking_positive_below_medium_is_low():
    row = _row("blocking", {"duration_ms": 1})

    signals = blocking.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.low


def test_blocking_zero_missing_or_non_numeric_duration_is_not_a_signal():
    rows = [
        _row("blocking", {"duration_ms": 0}),
        _row("blocking", {"duration_ms": None}),
        _row("blocking", {"duration_ms": "not-a-number"}),
        _row("blocking", {}),
    ]

    assert blocking.evaluate(_indexed(rows)) == []


# --- deadlocks --------------------------------------------------------------


def test_every_deadlock_is_high():
    rows = [_row("deadlocks", {"victim": 61}), _row("deadlocks", {"victim": 88})]

    signals = deadlocks.evaluate(_indexed(rows))

    assert [s.severity for s in signals] == [Severity.high, Severity.high]
    assert signals[0].evidence_ids == [evidence_id(rows[0])]


def test_no_deadlocks_is_no_signal():
    assert deadlocks.evaluate([]) == []


# --- top CPU / duration queries --------------------------------------------


def test_rank_one_cpu_query_above_high_threshold_is_high():
    row = _row("queries", {"category": "cpu", "rank": 1,
                           "metric_value": QUERY_HIGH_METRIC_US})

    signals = queries.evaluate(_indexed([row]))

    assert len(signals) == 1
    assert signals[0].severity is Severity.high
    assert signals[0].area == queries.AREA


def test_rank_one_duration_query_between_thresholds_is_medium():
    row = _row("queries", {"category": "duration", "rank": 1,
                           "metric_value": QUERY_MEDIUM_METRIC_US})

    signals = queries.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.medium


def test_rank_one_query_below_medium_is_low():
    row = _row("queries", {"category": "cpu", "rank": 1, "metric_value": 1})

    signals = queries.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.low


def test_non_top_or_non_cpu_duration_query_is_not_a_signal():
    rows = [
        _row("queries", {"category": "cpu", "rank": 2, "metric_value": 10 ** 9}),
        _row("queries", {"category": "reads", "rank": 1, "metric_value": 10 ** 9}),
        _row("queries", {"category": "writes", "rank": 1, "metric_value": 10 ** 9}),
        _row("queries", {"category": "cpu", "rank": 1}),
    ]

    assert queries.evaluate(_indexed(rows)) == []


# --- SQL Agent jobs ---------------------------------------------------------


def test_repeated_job_failure_is_high():
    row = _row("agent_jobs", {"repeated": True, "job_name": "Nightly"})

    signals = jobs.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.high
    assert "Repeated" in signals[0].description


def test_single_job_failure_is_medium():
    row = _row("agent_jobs", {"repeated": False, "job_name": "Nightly"})

    signals = jobs.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.medium


def test_no_job_failures_is_no_signal():
    assert jobs.evaluate([]) == []


# --- SQL Server errors ------------------------------------------------------


def test_error_severity_bands_map_to_high_medium_low():
    rows = [
        _row("errors", {"severity_code": ERROR_SEVERITY_HIGH}),
        _row("errors", {"severity_code": 16}),
        _row("errors", {"severity_code": ERROR_SEVERITY_MEDIUM}),
        _row("errors", {"severity_code": 1}),
        _row("errors", {"severity_code": 10}),
    ]

    signals = errors.evaluate(_indexed(rows))

    assert [s.severity for s in signals] == [
        Severity.high,
        Severity.medium,
        Severity.medium,
        Severity.low,
        Severity.low,
    ]


def test_error_unknown_severity_is_low_and_label_is_a_fallback():
    rows = [
        _row("errors", {}),
        _row("errors", {"severity": "high"}),
        _row("errors", {"severity": "medium"}),
    ]

    signals = errors.evaluate(_indexed(rows))

    assert [s.severity for s in signals] == [
        Severity.low,
        Severity.high,
        Severity.medium,
    ]


def test_no_error_log_entries_is_no_signal():
    assert errors.evaluate([]) == []


# --- availability -----------------------------------------------------------


def test_any_unavailable_database_is_high():
    row = _row("availability", {"database_name": "App", "state": "offline"})

    signals = availability.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.high
    assert "App" in signals[0].description


def test_no_unavailable_databases_is_no_signal():
    assert availability.evaluate([]) == []


# --- storage ----------------------------------------------------------------


def test_free_space_below_ten_percent_is_high():
    row = _row("storage", {"free_ratio": 0.099})

    signals = storage.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.high
    assert signals[0].area == storage.AREA


def test_free_space_between_ten_and_twenty_percent_is_low():
    row = _row("storage", {"free_ratio": STORAGE_FREE_HIGH_RATIO})

    signals = storage.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.low


def test_free_space_at_or_above_twenty_percent_is_not_a_signal():
    rows = [
        _row("storage", {"free_ratio": STORAGE_FREE_LOW_RATIO}),
        _row("storage", {"free_ratio": 0.9}),
        _row("storage", {}),
    ]

    assert storage.evaluate(_indexed(rows)) == []


def test_storage_falls_back_to_free_percent_when_ratio_is_absent():
    row = _row("storage", {"free_percent": 5.0})

    signals = storage.evaluate(_indexed([row]))

    assert signals[0].severity is Severity.high


# --- vocabulary -------------------------------------------------------------


def test_rule_areas_are_all_in_the_catalog_vocabulary():
    assert set(AREA_ORDER) <= set(CATALOG_ISSUE_AREAS)
    assert "cpu" in CATALOG_ISSUE_AREAS
