"""Blocking / duration rule (plan.md §4 area 1, #17).

Turns the #13 current-blocking rows into signals by wait duration: any strictly
positive wait is a signal, ``medium`` at :data:`BLOCKING_MEDIUM_MS` and ``high``
at :data:`BLOCKING_HIGH_MS`. A zero, missing, or non-numeric wait is not a
signal, so a healthy snapshot (zero rows) and an instant/gap row both produce
nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import as_number, detail
from app.rules.signals import IndexedEvidence, Severity, Signal
from app.rules.thresholds import BLOCKING_HIGH_MS, BLOCKING_MEDIUM_MS

#: Issue area every signal from this rule belongs to.
AREA = "blocking"


def severity_for(duration_ms: float | None) -> Severity | None:
    """The severity for a blocking wait, or ``None`` when it is not a signal."""
    if duration_ms is None or duration_ms <= 0:
        return None
    if duration_ms >= BLOCKING_HIGH_MS:
        return Severity.high
    if duration_ms >= BLOCKING_MEDIUM_MS:
        return Severity.medium
    return Severity.low


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One signal per blocked session whose wait crosses the low threshold."""
    signals: list[Signal] = []
    for item in rows:
        duration = as_number(detail(item, "duration_ms"))
        severity = severity_for(duration)
        if severity is None or duration is None:
            continue
        blocked = detail(item, "blocked_session_id", "unknown")
        blocker = detail(item, "blocking_session_id", "unknown")
        signals.append(
            Signal(
                area=AREA,
                severity=severity,
                description=(
                    f"Session {blocked} blocked by session {blocker} "
                    f"for {duration:.0f} ms."
                ),
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
