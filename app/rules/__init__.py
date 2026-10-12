"""Built-in, read-only rules engine (#17).

Consumes the #13-#16 normalized evidence rows and emits
:class:`~app.rules.signals.Signal` objects with a string-enum severity and the
ids of the evidence rows that triggered them. Rules are pure functions of their
inputs: no I/O, no external AI, no cross-investigation baselines. Fixed
thresholds live in :mod:`app.rules.thresholds`.
"""

from app.rules.engine import (
    AREA_ORDER,
    RULES,
    SOURCE_TO_AREA,
    UNAVAILABLE_STATUSES,
    area_statuses,
    evaluate,
    evidence_id,
    index_evidence,
)
from app.rules.signals import ISSUE_AREAS, IndexedEvidence, Severity, Signal

__all__ = [
    "AREA_ORDER",
    "ISSUE_AREAS",
    "RULES",
    "SOURCE_TO_AREA",
    "UNAVAILABLE_STATUSES",
    "IndexedEvidence",
    "Severity",
    "Signal",
    "area_statuses",
    "evaluate",
    "evidence_id",
    "index_evidence",
]
