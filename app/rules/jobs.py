"""SQL Agent job-failure rule (plan.md §4 area 3, #17).

#15 emits one row per failed run and sets ``repeated`` when the same job failed
more than once in the window. A repeated failure is ``high``; a single failure
is ``medium``. A clean window has no rows, so it yields no signal.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import detail
from app.rules.signals import IndexedEvidence, Severity, Signal

#: Issue area every signal from this rule belongs to.
AREA = "agent_jobs"


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One signal per failed job run, high when the job failed repeatedly."""
    signals: list[Signal] = []
    for item in rows:
        repeated = bool(detail(item, "repeated", False))
        name = detail(item, "job_name", "unknown job")
        label = "Repeated" if repeated else "Single"
        signals.append(
            Signal(
                area=AREA,
                severity=Severity.high if repeated else Severity.medium,
                description=f"{label} SQL Agent job failure: '{name}'.",
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
