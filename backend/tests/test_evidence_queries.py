"""Unit tests for the resource-consuming query evidence collector (#14).

Driven with fixture DMV rows (``tests/fixtures/evidence/query_stats.json``) and
the #6 :class:`~app.sql.connector.Row` type, so the suite runs with no SQL
Server. The live-server path is the gated ``tests/test_evidence_queries_db.py``.

Covered: top-five ranking per category, a query reported once per category it is
expensive in, an exact tie broken by execution count then SQL text, a true tie
kept in DMV input order, fewer-than-five returns, an empty result set that is
still ``collected``, null/negative counters, category labeling, and failure
handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.diagnostics.catalog import build_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.queries import (
    AREA,
    CATEGORIES,
    CATEGORY_METRICS,
    SOURCE_ID,
    TOP_N,
    QueriesCollector,
    collect_queries,
    rank_queries,
)
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


def _sql_texts(findings) -> list[str]:
    return [finding.details["sql_text"] for finding in findings]


def _by_category(findings, category):
    return [f for f in findings if f.details["category"] == category]


# --- ranking ----------------------------------------------------------------


def test_top_five_per_category_with_category_labels():
    rows = _load_rows("query_stats.json")
    registry = _registry()

    findings = QueriesCollector().collect(SourceEvidence(SOURCE_ID, rows), registry)

    assert len(findings) == len(CATEGORIES) * TOP_N
    for category in CATEGORIES:
        in_category = _by_category(findings, category)
        assert len(in_category) == TOP_N
        assert [f.details["rank"] for f in in_category] == [1, 2, 3, 4, 5]
        assert all(f.details["metric"] == CATEGORY_METRICS[category] for f in in_category)
        assert all(f.area == AREA for f in findings)
        assert all(f.available is True for f in findings)
        assert all(f.timestamp is None for f in findings)
        assert all(f.status is SourceStatus.collected for f in findings)
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_cpu_ranking_ties_break_by_execution_count_then_sql_text():
    rows = _load_rows("query_stats.json")

    findings = rank_queries(rows)
    cpu = _sql_texts(_by_category(findings, "cpu"))

    assert cpu == [
        "SELECT cpu_heavy",
        "SELECT tie_a",
        "SELECT tie_b",
        "SELECT multi_category",
        "SELECT duration_heavy",
    ]


def test_duration_reads_and_writes_rankings():
    rows = _load_rows("query_stats.json")
    findings = rank_queries(rows)

    assert _sql_texts(_by_category(findings, "duration")) == [
        "SELECT duration_heavy",
        "SELECT multi_category",
        "SELECT cpu_heavy",
        "SELECT reads_heavy",
        "SELECT writes_heavy",
    ]
    assert _sql_texts(_by_category(findings, "reads")) == [
        "SELECT reads_heavy",
        "SELECT multi_category",
        "SELECT duration_heavy",
        "SELECT cpu_heavy",
        "SELECT null_metrics",
    ]
    assert _sql_texts(_by_category(findings, "writes")) == [
        "SELECT writes_heavy",
        "SELECT multi_category",
        "SELECT tie_a",
        "SELECT tie_b",
        "SELECT negative_cpu",
    ]


def test_query_expensive_in_several_categories_is_reported_once_per_category():
    rows = _load_rows("query_stats.json")

    findings = rank_queries(rows)
    multi = [f for f in findings if f.details["sql_text"] == "SELECT multi_category"]

    assert {f.details["category"] for f in multi} == set(CATEGORIES)
    assert len(multi) == len(CATEGORIES)
    values = {f.details["category"]: f.details["metric_value"] for f in multi}
    assert values == {"cpu": 800, "duration": 8000, "reads": 7000, "writes": 6000}


def test_true_tie_keeps_dmv_input_order():
    items = [
        {
            "total_worker_time": 500,
            "total_elapsed_time": 500,
            "total_logical_reads": 500,
            "total_logical_writes": 500,
            "execution_count": 4,
            "sql_text": "SELECT dup",
            "sql_handle": "handle_first",
        },
        {
            "total_worker_time": 500,
            "total_elapsed_time": 500,
            "total_logical_reads": 500,
            "total_logical_writes": 500,
            "execution_count": 4,
            "sql_text": "SELECT dup",
            "sql_handle": "handle_second",
        },
    ]

    findings = rank_queries(_rows_from(items))
    cpu = _by_category(findings, "cpu")

    assert [f.details["sql_handle"] for f in cpu] == ["handle_first", "handle_second"]


def test_negative_and_null_metrics_are_never_ranked():
    rows = _load_rows("query_stats.json")

    findings = rank_queries(rows)
    cpu_texts = _sql_texts(_by_category(findings, "cpu"))
    duration_texts = _sql_texts(_by_category(findings, "duration"))

    assert "SELECT null_metrics" not in cpu_texts
    assert "SELECT null_metrics" not in duration_texts
    assert "SELECT negative_cpu" not in cpu_texts
    assert "SELECT null_metrics" in _sql_texts(_by_category(findings, "reads"))
    assert "SELECT negative_cpu" in _sql_texts(_by_category(findings, "writes"))


def test_fewer_than_five_rows_returns_that_many():
    rows = _rows_from(
        [
            {
                "total_worker_time": 30,
                "total_elapsed_time": 300,
                "total_logical_reads": 3000,
                "total_logical_writes": 300,
                "execution_count": 1,
                "sql_text": "SELECT q1",
            },
            {
                "total_worker_time": 20,
                "total_elapsed_time": 200,
                "total_logical_reads": 2000,
                "total_logical_writes": 200,
                "execution_count": 1,
                "sql_text": "SELECT q2",
            },
            {
                "total_worker_time": 10,
                "total_elapsed_time": 100,
                "total_logical_reads": 1000,
                "total_logical_writes": 100,
                "execution_count": 1,
                "sql_text": "SELECT q3",
            },
        ]
    )
    registry = _registry()

    findings = collect_queries(SourceEvidence(SOURCE_ID, rows), registry)

    assert len(findings) == len(CATEGORIES) * 3
    assert all(len(_by_category(findings, c)) == 3 for c in CATEGORIES)
    assert registry.get(SOURCE_ID).status is SourceStatus.collected


def test_empty_result_is_zero_rows_but_collected():
    registry = _registry()

    findings = collect_queries(SourceEvidence(SOURCE_ID, []), registry)

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is SourceStatus.collected
    assert "0 ranked query row" in source.note


def test_repeated_collection_is_stable():
    rows = _load_rows("query_stats.json")

    first = [f.model_dump() for f in rank_queries(rows)]
    second = [f.model_dump() for f in rank_queries(rows)]

    assert first == second


# --- failures ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        (SourceStatus.unavailable_setting, SourceStatus.unavailable_setting),
        (SourceStatus.unavailable_permission, SourceStatus.unavailable_permission),
        (SourceStatus.unavailable_connection, SourceStatus.unavailable_connection),
    ],
)
def test_query_failure_is_never_collected(source_status, expected):
    registry = _registry()

    findings = QueriesCollector().collect(
        SourceEvidence(
            SOURCE_ID,
            [],
            status=QueryStatus.error,
            source_status=source_status,
            error="The request for procedure 'dm_exec_query_stats' failed",
        ),
        registry,
    )

    assert findings == []
    source = registry.get(SOURCE_ID)
    assert source.status is expected
    assert expected is not SourceStatus.collected
    assert "failed" in source.note


def test_unclassified_failure_defaults_to_unavailable_setting():
    registry = _registry()

    QueriesCollector().collect(
        SourceEvidence(
            SOURCE_ID,
            [],
            status=QueryStatus.error,
            source_status=SourceStatus.collected,
            error="DMVs are inaccessible on this server version",
        ),
        registry,
    )

    assert registry.get(SOURCE_ID).status is SourceStatus.unavailable_setting
