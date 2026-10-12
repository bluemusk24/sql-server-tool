"""SQL Server error severity rule (plan.md §4 area 3, #17).

Maps each #15 error-log row to a severity by its SQL Server severity code:
``high`` at :data:`ERROR_SEVERITY_HIGH` (>= 17), ``medium`` at
:data:`ERROR_SEVERITY_MEDIUM` (11-16), and ``low`` for everything lower or
unknown. When the numeric code is absent the collector's own label is used as a
fallback.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rules.fields import as_int, detail
from app.rules.signals import IndexedEvidence, Severity, Signal
from app.rules.thresholds import ERROR_SEVERITY_HIGH, ERROR_SEVERITY_MEDIUM

#: Issue area every signal from this rule belongs to.
AREA = "errors"

#: Fallback severity code for a collector label when the numeric code is absent.
_LABEL_CODES: dict[str, int] = {
    "high": ERROR_SEVERITY_HIGH,
    "medium": ERROR_SEVERITY_MEDIUM,
    "low": 0,
}


def severity_code(item: IndexedEvidence) -> int | None:
    """The numeric severity for ``item``: the code, else the label's code."""
    code = as_int(detail(item, "severity_code"))
    if code is not None:
        return code
    label = detail(item, "severity")
    if isinstance(label, str):
        return _LABEL_CODES.get(label.strip().lower())
    return None


def severity_for(code: int | None) -> Severity:
    """The severity for a SQL Server severity code (unknown is ``low``)."""
    if code is None:
        return Severity.low
    if code >= ERROR_SEVERITY_HIGH:
        return Severity.high
    if code >= ERROR_SEVERITY_MEDIUM:
        return Severity.medium
    return Severity.low


def evaluate(rows: Sequence[IndexedEvidence]) -> list[Signal]:
    """One signal per error-log entry, tagged by its SQL Server severity."""
    signals: list[Signal] = []
    for item in rows:
        code = severity_code(item)
        severity = severity_for(code)
        summary = detail(item, "error_text", "no error text was recorded")
        code_text = f" {code}" if code is not None else " unknown"
        signals.append(
            Signal(
                area=AREA,
                severity=severity,
                description=f"SQL Server error (severity{code_text}): {summary}.",
                evidence_ids=[item.evidence_id],
            )
        )
    return signals
