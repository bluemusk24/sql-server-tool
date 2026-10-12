"""Engine-level tests for the #17 rules engine.

Covers the contracts that sit above the individual rules: emitted-id references,
determinism/purity, the empty/null/duplicate edge cases, the ``unavailable-*``
source-status suppression, and a run over a complete set of collected evidence
from every #13-#16 source. No SQL Server is needed: the collectors are driven
with plain fixture rows.
"""

from __future__ import annotations

from pathlib import Path

from app.diagnostics.catalog import build_catalog
from app.evidence.availability import SOURCE_ID as AVAILABILITY_SOURCE
from app.evidence.availability import collect_availability
from app.evidence.blocking import CURRENT_SOURCE_ID as BLOCKING_SOURCE
from app.evidence.blocking import collect_blocking
from app.evidence.collectors import SourceEvidence
from app.evidence.deadlocks import SOURCE_ID as DEADLOCK_SOURCE
from app.evidence.deadlocks import collect_deadlocks
from app.evidence.errors import SOURCE_ID as ERROR_SOURCE
from app.evidence.errors import collect_errors
from app.evidence.jobs import SOURCE_ID as JOB_SOURCE
from app.evidence.jobs import collect_agent_jobs
from app.evidence.models import (
    DiagnosticRunResult,
    EvidenceRow,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.evidence.queries import SOURCE_ID as QUERY_SOURCE
from app.evidence.queries import collect_queries
from app.evidence.registry import EvidenceRegistry
from app.evidence.storage import SOURCE_ID as STORAGE_SOURCE
from app.evidence.storage import collect_storage
from app.rules import AREA_ORDER, area_statuses, evaluate, evidence_id
from app.rules.signals import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"
COLLECTED = SourceStatus.collected
PERMISSION = SourceStatus.unavailable_permission


def _row(area: str, details: dict | None = None, **kwargs) -> EvidenceRow:
    values: dict = {"area": area, "description": f"{area} evidence"}
    if details is not None:
        values["details"] = details
    values.update(kwargs)
    return EvidenceRow(**values)


def _blocking(duration_ms) -> EvidenceRow:
    return _row(
        "blocking",
        {
            "duration_ms": duration_ms,
            "blocked_session_id": 60,
            "blocking_session_id": 57,
        },
    )


def _deadlock(victim: int) -> EvidenceRow:
    return _row("deadlocks", {"victim": victim})


def _run_result() -> DiagnosticRunResult:
    """A complete #12 execution result: raw evidence from every #13-#16 source."""
    evidence = [
        QueryEvidence(
            query_id=BLOCKING_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[{"session_id": 60, "blocking_session_id": 57, "wait_time": 45_000}],
        ),
        QueryEvidence(
            query_id=DEADLOCK_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[
                {
                    "ring_data": (FIXTURES / "system_health_deadlocks.xml").read_text(
                        encoding="utf-8"
                    ),
                    "ring_timestamp": "2026-10-10T14:22:11.123",
                }
            ],
        ),
        QueryEvidence(
            query_id=QUERY_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[
                {
                    "total_worker_time": 70_000_000,
                    "execution_count": 1,
                    "sql_text": "SELECT 1",
                }
            ],
        ),
        QueryEvidence(
            query_id=JOB_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[
                {
                    "job_name": "Nightly",
                    "run_status": 0,
                    "run_date": 20261010,
                    "run_time": 100000,
                    "step_id": 0,
                }
            ],
        ),
        QueryEvidence(
            query_id=ERROR_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[
                {
                    "severity": 17,
                    "error_text": "serious failure",
                    "log_date": "2026-10-10T10:00:00",
                }
            ],
        ),
        QueryEvidence(
            query_id=AVAILABILITY_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[{"database_name": "App", "state": 6, "state_desc": "OFFLINE"}],
        ),
        QueryEvidence(
            query_id=STORAGE_SOURCE,
            database="master",
            status=QueryStatus.ok,
            rows=[
                {
                    "database_name": "App",
                    "physical_name": "app.mdf",
                    "available_bytes": 50,
                    "total_bytes": 1000,
                }
            ],
        ),
    ]
    return DiagnosticRunResult(investigation_id=1, status="succeeded", evidence=evidence)


def _all_sources(registry: EvidenceRegistry) -> list[EvidenceRow]:
    """Run every #13-#16 collector over a complete run result's raw evidence."""
    result = _run_result()
    by_id = {item.query_id: item for item in result.evidence}

    def _evidence(query_id: str) -> SourceEvidence:
        query = by_id[query_id]
        return SourceEvidence(
            source_id=query.query_id,
            rows=query.rows,
            status=query.status,
            source_status=SourceStatus.collected,
            database=query.database,
        )

    rows: list[EvidenceRow] = []
    rows += collect_blocking(_evidence(BLOCKING_SOURCE), registry)
    rows += collect_deadlocks(_evidence(DEADLOCK_SOURCE), registry)
    rows += collect_queries(_evidence(QUERY_SOURCE), registry)
    rows += collect_agent_jobs(_evidence(JOB_SOURCE), registry)
    rows += collect_errors(_evidence(ERROR_SOURCE), registry)
    rows += collect_availability(_evidence(AVAILABILITY_SOURCE), registry)
    rows += collect_storage(_evidence(STORAGE_SOURCE), registry)
    return rows


# --- basic contract ---------------------------------------------------------


def test_empty_evidence_yields_no_signals():
    assert evaluate([]) == []
    assert evaluate([], {}) == []


def test_signal_evidence_ids_reference_the_emitted_rows():
    high = _blocking(45_000)
    low = _blocking(2_000)

    signals = evaluate([high, low])

    assert len(signals) == 2
    assert signals[0].evidence_ids == [evidence_id(high)]
    assert signals[1].evidence_ids == [evidence_id(low)]


def test_signals_use_string_severities_only():
    signals = evaluate([_blocking(45_000)])

    assert signals[0].severity is Severity.high
    assert signals[0].severity.value == "high"
    dumped = signals[0].model_dump()
    assert set(dumped) == {"area", "severity", "description", "evidence_ids"}
    assert dumped["severity"] == "high"
    assert dumped["evidence_ids"] == [evidence_id(_blocking(45_000))]


def test_mixed_triggers_and_non_triggers_yield_exactly_the_expected_set():
    trigger = _blocking(45_000)
    non_trigger = _blocking(0)
    deadlock = _deadlock(61)
    rows = [trigger, non_trigger, deadlock]

    signals = evaluate(rows, {"blocking": COLLECTED, "deadlocks": COLLECTED})

    assert [(s.area, s.severity, s.evidence_ids) for s in signals] == [
        ("blocking", Severity.high, [evidence_id(trigger)]),
        ("deadlocks", Severity.high, [evidence_id(deadlock)]),
    ]


def test_collected_source_below_every_threshold_yields_no_signal():
    rows = [_blocking(0), _deadlock(61)]

    signals = evaluate(rows, {"blocking": COLLECTED})

    assert [s for s in signals if s.area == "blocking"] == []
    assert signals[0].area == "deadlocks"


# --- source status suppression ----------------------------------------------


def test_unavailable_area_status_suppresses_that_areas_rows():
    rows = [_blocking(45_000)]

    assert evaluate(rows, {"blocking": PERMISSION}) == []
    assert evaluate(rows, {"blocking": COLLECTED}) != []


def test_unavailable_source_status_accepts_source_keys():
    rows = [_blocking(45_000)]

    assert evaluate(rows, {BLOCKING_SOURCE: PERMISSION}) == []
    assert evaluate(rows, {BLOCKING_SOURCE: COLLECTED}) != []


def test_row_with_unavailable_status_is_never_used():
    row = _blocking(45_000).model_copy(update={"status": PERMISSION})

    assert evaluate([row]) == []


def test_gap_marker_is_not_used_to_fire_a_rule():
    gap = _row(
        "blocking",
        {"note": "no retained historical blocking evidence"},
        available=False,
        status=SourceStatus.not_applicable,
    )

    assert evaluate([gap], {"blocking": COLLECTED}) == []


# --- determinism, purity and no cross-investigation baseline ----------------


def test_output_is_stable_across_calls():
    rows = [_blocking(45_000), _deadlock(61)]

    first = [s.model_dump() for s in evaluate(rows, {"blocking": COLLECTED})]
    second = [s.model_dump() for s in evaluate(rows, {"blocking": COLLECTED})]

    assert first == second


def test_previous_runs_rows_do_not_change_the_output():
    current = [_blocking(45_000)]
    statuses = {"blocking": COLLECTED}

    baseline = evaluate(current, statuses)
    with_history = evaluate([_blocking(0), *current], statuses)

    assert [s.model_dump() for s in with_history] == [s.model_dump() for s in baseline]


def test_evaluate_does_not_read_another_investigations_rows():
    incident = [_blocking(45_000)]
    healthy = [_blocking(0)]

    evaluate(healthy, {"blocking": COLLECTED})
    result = evaluate(incident, {"blocking": COLLECTED})

    assert [s.model_dump() for s in result] == [
        s.model_dump() for s in evaluate(incident, {"blocking": COLLECTED})
    ]


# --- edge cases -------------------------------------------------------------


def test_duplicate_rows_yield_one_signal():
    duplicate = _blocking(45_000)

    signals = evaluate([duplicate, duplicate], {"blocking": COLLECTED})

    assert len(signals) == 1
    assert signals[0].evidence_ids == [evidence_id(duplicate)]


def test_missing_and_null_details_do_not_crash():
    rows = [
        _row("blocking", {}),
        _row("storage", {"free_ratio": None, "free_percent": None}),
        _row("queries", {"category": None, "rank": None, "metric_value": None}),
    ]

    assert evaluate(rows) == []


# --- complete execution result ----------------------------------------------


def test_engine_runs_on_a_complete_evidence_set_and_is_stable():
    registry = EvidenceRegistry(build_catalog())
    rows = _all_sources(registry)
    statuses = area_statuses(registry.all())

    signals = evaluate(rows, statuses)

    assert {signal.area for signal in signals} == set(AREA_ORDER)
    severity = {signal.area: signal.severity for signal in signals}
    assert severity["blocking"] is Severity.high
    assert severity["deadlocks"] is Severity.high
    assert severity["queries"] is Severity.high
    assert severity["agent_jobs"] is Severity.medium
    assert severity["errors"] is Severity.high
    assert severity["availability"] is Severity.high
    assert severity["storage"] is Severity.high

    again = evaluate(rows, statuses)
    assert [s.model_dump() for s in again] == [s.model_dump() for s in signals]


def test_area_statuses_accepts_the_run_inventory_sequence():
    registry = EvidenceRegistry(build_catalog())
    _all_sources(registry)

    statuses = area_statuses(registry.all())

    assert statuses["blocking"] is COLLECTED
    assert statuses["storage"] is COLLECTED
