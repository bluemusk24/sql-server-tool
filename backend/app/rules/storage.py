"""Storage-pressure rule (plan.md §4 area 4, #17).

A file below :data:`STORAGE_FREE_HIGH_RATIO` (10%) free is ``high``; the
documented extra band below :data:`STORAGE_FREE_LOW_RATIO` (20%) free is ``low``.
At or above 20% free there is no signal. The free fraction is read from the
#16 row's ``free_ratio`` and falls back to ``free_percent``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import as_number, detail
from app.rules.signals import IndexedEvidence, Severity, Signal
from app.rules.thresholds import STORAGE_FREE_HIGH_RATIO, STORAGE_FREE_LOW_RATIO

#: Issue area every signal from this rule belongs to.
AREA = "storage"


def free_ratio(item: IndexedEvidence) -> float | None:
    """The file's free-space fraction (0..1), or ``None`` when unknown."""
    ratio = as_number(detail(item, "free_ratio"))
    if ratio is not None and ratio >= 0:
        return ratio
    percent = as_number(detail(item, "free_percent"))
    if percent is not None and percent >= 0:
        return percent / 100.0
    return None


def severity_for(ratio: float | None) -> Severity | None:
    """The severity for a free-space fraction, or ``None`` when it is not a signal."""
    if ratio is None:
        return None
    if ratio < STORAGE_FREE_HIGH_RATIO:
        return Severity.high
    if ratio < STORAGE_FREE_LOW_RATIO:
        return Severity.low
    return None


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One signal per file whose free space crosses a threshold."""
    signals: list[Signal] = []
    for item in rows:
        ratio = free_ratio(item)
        severity = severity_for(ratio)
        if severity is None or ratio is None:
            continue
        name = detail(item, "database_name", "unknown")
        path = detail(item, "physical_name", "unknown")
        signals.append(
            Signal(
                area=AREA,
                severity=severity,
                description=(
                    f"Database '{name}' file '{path}' has {ratio * 100:.2f}% free space."
                ),
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
