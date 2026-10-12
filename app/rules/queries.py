"""Top-CPU / top-duration query rule (plan.md §4 area 2, #17).

Only a query that ranks first in the ``cpu`` or ``duration`` category is a
signal (#14 already ranks the top five per category). Severity comes from the
cumulative metric magnitude: ``high`` at :data:`QUERY_HIGH_METRIC_US`, ``medium``
at :data:`QUERY_MEDIUM_METRIC_US`, otherwise ``low``. Reads and writes rankings
are not signals.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import as_int, as_number, detail
from app.rules.signals import IndexedEvidence, Severity, Signal
from app.rules.thresholds import (
    QUERY_HIGH_METRIC_US,
    QUERY_MEDIUM_METRIC_US,
    QUERY_TOP_RANK,
)

#: Issue area every signal from this rule belongs to.
AREA = "queries"

#: The resource categories whose rank-1 query is a signal.
TOP_CATEGORIES = ("cpu", "duration")


def severity_for(metric_value: float | None) -> Severity | None:
    """The severity for a rank-1 query metric, or ``None`` when it is not a signal."""
    if metric_value is None or metric_value < 0:
        return None
    if metric_value >= QUERY_HIGH_METRIC_US:
        return Severity.high
    if metric_value >= QUERY_MEDIUM_METRIC_US:
        return Severity.medium
    return Severity.low


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One signal per rank-1 CPU or duration query."""
    signals: list[Signal] = []
    for item in rows:
        category = detail(item, "category")
        if category not in TOP_CATEGORIES:
            continue
        if as_int(detail(item, "rank")) != QUERY_TOP_RANK:
            continue
        metric = as_number(detail(item, "metric_value"))
        severity = severity_for(metric)
        if severity is None or metric is None:
            continue
        signals.append(
            Signal(
                area=AREA,
                severity=severity,
                description=(
                    f"Top {category} query (rank {QUERY_TOP_RANK}) "
                    f"used {metric:.0f} microseconds."
                ),
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
