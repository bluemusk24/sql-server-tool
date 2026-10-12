"""Value helpers shared by the #17 rule modules.

A rule reads fields from an :class:`~app.evidence.models.EvidenceRow`'s
``details`` mapping, which the #13-#16 collectors populate from raw DMV rows.
Those fields may be missing, ``None``, a numeric string, or the wrong type. These
helpers keep the missing/null coercion identical across every rule.
"""

from __future__ import annotations

from typing import Any

from app.rules.signals import IndexedEvidence


def detail(item: IndexedEvidence, key: str, default: Any = None) -> Any:
    """The ``key`` value from ``item``'s details, or ``default`` when absent/null."""
    value = item.row.details.get(key, default)
    return default if value is None else value


def as_number(raw: Any) -> float | None:
    """Return ``raw`` as a ``float``, or ``None`` when it is not numeric.

    Booleans are rejected (``True`` is not a metric), as are blank/non-numeric
    strings.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def as_int(raw: Any) -> int | None:
    """Return ``raw`` as an ``int``, or ``None`` when it is not an integer."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None
