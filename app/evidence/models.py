"""Pydantic models for raw diagnostic evidence and its source inventory (#12).

These models are the run payload schema: :func:`app.execution.run_diagnostic`
returns a :class:`DiagnosticRunResult`, the #11 worker serialises it, and the
reporting/persistence layers (#19, #21) read the same shape. They deliberately
carry no credential material: only connection-free facts (query id, database,
row count, duration, status, error text) and the raw rows the server returned.

Two status dimensions are modelled:

* :class:`QueryStatus` is the per-execution outcome (``ok``/``error``/
  ``timeout``/``skipped``), and
* :class:`SourceStatus` is the per-source inventory state used to decide
  whether a summary may claim a source was checked.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QueryStatus(str, Enum):
    """Outcome of executing one catalog query against one target database."""

    ok = "ok"
    error = "error"
    timeout = "timeout"
    skipped = "skipped"


class SourceStatus(str, Enum):
    """Inventory state of one evidence source (one per catalog id).

    ``collected`` is the only state that lets a summary claim a source was
    checked. ``unavailable-connection`` covers an unreachable server/database;
    ``unavailable-permission`` a denied login or query; ``unavailable-setting``
    any other server-side reason (version/setting). ``not-applicable`` is only
    set from a per-source applicability predicate.
    """

    collected = "collected"
    unavailable_permission = "unavailable-permission"
    unavailable_connection = "unavailable-connection"
    unavailable_setting = "unavailable-setting"
    not_applicable = "not-applicable"


class QueryEvidence(BaseModel):
    """Raw evidence from executing one catalog query against one database.

    ``rows`` holds the raw result rows as ordered mappings keyed by column
    name. ``row_count`` is the true count before any truncation; when a result
    exceeds the engine's cap, ``rows`` is truncated and ``truncated`` is set.
    ``error`` is ``None`` on success and a credential-free message otherwise.
    """

    query_id: str
    database: str
    status: QueryStatus
    row_count: int = 0
    duration_ms: float = 0.0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    truncated: bool = False


class EvidenceSource(BaseModel):
    """One evidence source's state in the run inventory, keyed by catalog id."""

    query_id: str
    title: str
    category: str
    issue_area: str
    status: SourceStatus
    note: str = ""


class DiagnosticRunResult(BaseModel):
    """Everything a diagnostic run returns: raw evidence plus the inventory.

    ``sources`` is the serialised evidence registry. ``status`` is the run's
    terminal state; a run that records unavailable sources still completes as
    ``succeeded`` (per-query failures are evidence, not run failures).
    """

    investigation_id: int
    status: str = "succeeded"
    evidence: list[QueryEvidence] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
