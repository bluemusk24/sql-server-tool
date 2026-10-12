"""Shared contract for the category evidence collectors (#13-#16).

The #12 execution engine produces raw :class:`~app.evidence.models.QueryEvidence`
(one result set per catalog query) and an :class:`EvidenceRegistry` inventory.
A *collector* turns those raw rows into the structured
:class:`~app.evidence.models.EvidenceRow` findings the rules engine (#17)
consumes, and records its source's status in the registry.

One collector owns one catalog source id. It receives the :class:`SourceEvidence`
for that source -- the raw rows plus the #12 query outcome and source status --
and returns zero or more :class:`EvidenceRow` records:

* current-state sources (blocking now) emit one row per finding and are
  ``collected`` even when there are no findings;
* historical sources emit a gap marker when the server retained nothing, so the
  report marks the missing evidence instead of claiming it was checked;
* any query failure yields zero rows and an ``unavailable-*`` source status with
  the (credential-free) error note, and is never reported as ``collected``.

Collectors only read data the engine already fetched; they never execute SQL and
never create or change extended-events sessions (plan.md §4). This module is the
one place #13-#16 share so their inputs, outputs, and failure handling stay
identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry


@dataclass(frozen=True)
class SourceEvidence:
    """One source's raw execution evidence in collector-input form.

    ``rows`` are the ordered mappings the engine stored (each supports
    ``row["column"]``: both the #6 :class:`~app.sql.connector.Row` type and the
    plain ``dict`` rows on :class:`~app.evidence.models.QueryEvidence`).
    ``status`` is the #6 query outcome; ``source_status`` is the #12 inventory
    status the engine already classified from it (so a denied query stays
    ``unavailable-permission``); ``error`` is the credential-free error note.
    """

    source_id: str
    rows: Sequence[Any] = ()
    status: QueryStatus = QueryStatus.ok
    source_status: SourceStatus = SourceStatus.collected
    error: str | None = None
    database: str = ""


@runtime_checkable
class Collector(Protocol):
    """A category collector for one catalog source id."""

    #: The catalog query id (or synthetic source id) this collector owns.
    source_id: str

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[Any]:
        """Record ``evidence``'s source status and return structured rows."""


#: Note used when a query failed and no evidence could be collected.
GENERIC_FAILURE_NOTE = "Source query failed; evidence was not collected."


def value(row: Any, key: str, default: Any = None) -> Any:
    """Return ``row[key]`` or ``default`` when the column is missing/null.

    Works for both the #6 :class:`~app.sql.connector.Row` (name or index) and
    the plain ``dict`` rows the engine stores. A missing column or a key/type
    mismatch never raises: callers get ``default``.
    """
    try:
        result = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if result is None else result


def failure_status(evidence: SourceEvidence) -> tuple[SourceStatus, str]:
    """Map a failed source onto its ``unavailable-*`` status and error note.

    Preserves ``unavailable-permission`` and ``unavailable-connection`` exactly
    as the #12 engine classified them; every other failure becomes
    ``unavailable-setting``. A failure is never reported as ``collected``.
    """
    note = evidence.error or GENERIC_FAILURE_NOTE
    if evidence.source_status is SourceStatus.unavailable_permission:
        return SourceStatus.unavailable_permission, note
    if evidence.source_status is SourceStatus.unavailable_connection:
        return SourceStatus.unavailable_connection, note
    return SourceStatus.unavailable_setting, note
