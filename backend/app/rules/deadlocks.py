"""Deadlock rule (plan.md §4 area 1, #17).

Flags every deadlock found in retained evidence as ``high`` -- there is no
threshold because the presence of a deadlock graph is itself the incident.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import detail
from app.rules.signals import IndexedEvidence, Severity, Signal

#: Issue area every signal from this rule belongs to.
AREA = "deadlocks"


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One ``high`` signal per retained deadlock."""
    signals: list[Signal] = []
    for item in rows:
        victim = detail(item, "victim", "unknown")
        signals.append(
            Signal(
                area=AREA,
                severity=Severity.high,
                description=f"Deadlock detected (victim session {victim}).",
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
