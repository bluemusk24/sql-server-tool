"""Slow / resource-intensive query evidence collector (plan.md §4 area 2, #14).

Normalises the raw DMV rows of the catalog source ``top_elapsed_queries``
(:mod:`app.diagnostics.queries.slow_queries`) into structured
:class:`~app.evidence.models.EvidenceRow` findings: the top :data:`TOP_N`
completed queries by each *available* resource category -- ``cpu``,
``duration``, ``reads`` and ``writes`` -- regardless of how long each query ran.

One raw ``sys.dm_exec_query_stats`` row carries four cumulative counters; the
collector ranks it independently against the other rows per counter, so a query
that is expensive in several categories is reported once under each category's
own ranking (plan.md §4 "regardless of runtime"; #14 groomed spec).

Ordering is this module's single responsibility: the DMV query filters to the
#9 lookback window and returns the raw counters, and :func:`rank_queries`
performs all ranking. Ties (equal metric value) are broken deterministically by
execution count, then SQL text, then the input row order from the DMV, so the
same fixture always yields the same rows. A null, missing, or negative counter
is treated as unavailable for that category and never ranked.

The collector only reads rows the #12 engine already fetched; it never executes
SQL and never changes a session (plan.md §4). Category sources are DMV-only:
Query Store is explicitly out of scope, and any failed source query is recorded
as ``unavailable-*`` (never ``collected``).
"""

from __future__ import annotations

from typing import Any

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry

#: Catalog id of the resource-consuming query DMV source (#8).
SOURCE_ID = "top_elapsed_queries"

#: Issue area every emitted row belongs to.
AREA = "queries"

#: Fixed ranking limit shared by #14-#17 (the top rows per resource category).
TOP_N = 5

#: Resource categories, in the order their findings are emitted.
CATEGORIES = ("cpu", "duration", "reads", "writes")

#: DMV column that carries each category's cumulative ranking metric.
CATEGORY_METRICS: dict[str, str] = {
    "cpu": "total_worker_time",
    "duration": "total_elapsed_time",
    "reads": "total_logical_reads",
    "writes": "total_logical_writes",
}

#: Longest SQL text kept in a row's details; longer text is truncated.
SQL_TEXT_LIMIT = 500

#: Longest single-line query summary used in the description.
SUMMARY_LIMIT = 200


def _to_number(raw: Any) -> int | float | None:
    """Return ``raw`` as an ``int``/``float``, or ``None`` when not numeric.

    Booleans are rejected (``True`` is not a metric). Integral values are
    returned as ``int`` so DMV counters do not render as ``800.0``.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
    else:
        try:
            number = float(str(raw).strip())
        except (TypeError, ValueError):
            return None
    return int(number) if number.is_integer() else number


def _metric_value(row: Any, column: str) -> int | float | None:
    """The non-negative ranking metric from ``row``, or ``None`` if unavailable."""
    number = _to_number(value(row, column, None))
    if number is None or number < 0:
        return None
    return number


def _execution_count(row: Any) -> int:
    """The execution count used as the first tie-breaker (0 when unavailable)."""
    number = _to_number(value(row, "execution_count", None))
    if number is None or number < 0:
        return 0
    return int(number)


def _sql_text(row: Any) -> str:
    """The query text for ``row`` (``sql_text``/``query_text``), or an empty string."""
    text = value(row, "sql_text", None)
    if text is None:
        text = value(row, "query_text", None)
    return "" if text is None else str(text).strip()


def _collapse(text: str, limit: int) -> str:
    """Collapse whitespace to one line and truncate to ``limit`` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."


def _finding(
    *,
    category: str,
    column: str,
    metric_value: float,
    execution_count: int,
    sql_text: str,
    rank: int,
    row: Any,
) -> EvidenceRow:
    """Build one ranked-query :class:`EvidenceRow`."""
    summary = _collapse(sql_text, SUMMARY_LIMIT) or "query text unavailable"
    return EvidenceRow(
        area=AREA,
        timestamp=None,
        description=(
            f"{category} rank {rank}: {column}={metric_value} over "
            f"{execution_count} execution(s); {summary}."
        ),
        status=SourceStatus.collected,
        details={
            "category": category,
            "metric": column,
            "metric_value": metric_value,
            "rank": rank,
            "execution_count": execution_count,
            "sql_text": _collapse(sql_text, SQL_TEXT_LIMIT),
            "query_summary": summary,
            "sql_handle": value(row, "sql_handle", None),
            "query_hash": value(row, "query_hash", None),
        },
    )


def rank_queries(rows: list[Any]) -> list[EvidenceRow]:
    """Rank raw DMV rows into the top :data:`TOP_N` findings per category.

    Every row is considered in each category whose metric it actually carries;
    categories with no rows yield no findings. The returned rows are ordered by
    category (``cpu``, ``duration``, ``reads``, ``writes``) and then by rank.
    """
    findings: list[EvidenceRow] = []
    for category in CATEGORIES:
        column = CATEGORY_METRICS[category]
        candidates: list[tuple[int | float, int, str, int, Any]] = []
        for index, row in enumerate(rows):
            metric_value = _metric_value(row, column)
            if metric_value is None:
                continue
            candidates.append(
                (metric_value, _execution_count(row), _sql_text(row), index, row)
            )
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
        for rank, (
            metric_value,
            execution_count,
            sql_text,
            _index,
            row,
        ) in enumerate(candidates[:TOP_N], start=1):
            findings.append(
                _finding(
                    category=category,
                    column=column,
                    metric_value=metric_value,
                    execution_count=execution_count,
                    sql_text=sql_text,
                    rank=rank,
                    row=row,
                )
            )
    return findings


class QueriesCollector:
    """Top resource-consuming queries per category; zero rows stays ``collected``."""

    source_id = SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        rows = list(evidence.rows)
        findings = rank_queries(rows)
        categories = len({finding.details["category"] for finding in findings})
        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} ranked query row(s) across {categories} category(ies).",
        )
        return findings


def collect_queries(
    evidence: SourceEvidence, registry: EvidenceRegistry
) -> list[EvidenceRow]:
    """Run the resource-consuming query collector and return its structured rows."""
    return QueriesCollector().collect(evidence, registry)
