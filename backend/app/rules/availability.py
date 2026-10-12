"""Database availability rule (plan.md §4 area 4, #17).

Any database the #16 collector reports in a non-normal state is ``high``: an
unavailable database is always a primary problem. A healthy server produces no
rows and therefore no signal.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import detail
from app.rules.signals import IndexedEvidence, Severity, Signal

#: Issue area every signal from this rule belongs to.
AREA = "availability"


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One ``high`` signal per unavailable database."""
    signals: list[Signal] = []
    for item in rows:
        name = detail(item, "database_name", "unknown")
        state = detail(item, "state", "unknown")
        signals.append(
            Signal(
                area=AREA,
                severity=Severity.high,
                description=f"Database '{name}' is not available (state: {state}).",
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
