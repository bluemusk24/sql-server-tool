"""Normalized rule signals (#17).

A :class:`Signal` is the only thing the rules layer emits: an issue ``area`` (the
#8/#13-#16 vocabulary), a string-enum :class:`Severity`, a human description, and
the ids of the evidence rows that triggered it. Rules never emit numeric
severities or a confidence score: confidence is #18's job.

:class:`IndexedEvidence` pairs a raw
:class:`~app.evidence.models.EvidenceRow` with the deterministic id the engine
emitted for it, so a rule can reference the rows it used without the #13-#16 row
model itself carrying an id.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from app.evidence.models import EvidenceRow

#: The issue-area vocabulary shared with the catalog (#8) and collectors (#13-#16).
ISSUE_AREAS = (
    "blocking",
    "deadlocks",
    "queries",
    "cpu",
    "agent_jobs",
    "errors",
    "availability",
    "storage",
)


class Severity(str, Enum):
    """The only severities the rules layer may emit."""

    high = "high"
    medium = "medium"
    low = "low"


class Signal(BaseModel):
    """One normalized rule signal, referencing evidence rows by emitted id."""

    area: str
    severity: Severity
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class IndexedEvidence:
    """An evidence row paired with the id the engine emitted for it."""

    evidence_id: str
    row: EvidenceRow
