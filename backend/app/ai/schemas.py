"""Schema and shared constants for the #18 AI correlation verdict.

The :class:`Verdict` is the single, provider-agnostic shape every consumer
(#19 report assembly, #26 synthetic evaluation) reads. Confidence is always
shipped as **both** a percentage and a derived :class:`ConfidenceLabel`, and the
inconclusive boundary is the one shared :data:`INCONCLUSIVE_THRESHOLD` (``0.5``),
so no consumer can redefine it.

The prompt is assembled only from #17 :class:`~app.rules.signals.Signal` objects
and #12 evidence summaries; the models here carry no credential field.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.evidence.models import EvidenceSource, SourceStatus
from app.rules.signals import Severity, Signal

#: A result at or below this confidence fraction is inconclusive (plan.md §5).
#: Kept as a fraction so the "below 50%" rule has exactly one definition here.
INCONCLUSIVE_THRESHOLD = 0.5

#: The primary-cause description reported when a result is inconclusive.
INSUFFICIENT_EVIDENCE_DESCRIPTION = "insufficient evidence"

#: At most this many alternative causes are ever reported.
MAX_ALTERNATIVES = 3

#: Inclusive upper bounds for the ``low`` and ``medium`` confidence bands.
CONFIDENCE_LOW_MAX = 49
CONFIDENCE_MEDIUM_MAX = 79


class ConfidenceLabel(str, Enum):
    """The derived low/medium/high confidence band."""

    low = "low"
    medium = "medium"
    high = "high"


def confidence_label(percent: int) -> ConfidenceLabel:
    """Map a 0-100 percentage onto its band: 0-49 low, 50-79 medium, 80-100 high."""
    if percent <= CONFIDENCE_LOW_MAX:
        return ConfidenceLabel.low
    if percent <= CONFIDENCE_MEDIUM_MAX:
        return ConfidenceLabel.medium
    return ConfidenceLabel.high


def is_inconclusive(
    confidence_percent: int, evidence_quality_insufficient: bool
) -> bool:
    """Whether a result must be marked inconclusive (low confidence or thin evidence)."""
    return (confidence_percent / 100.0) < INCONCLUSIVE_THRESHOLD or (
        evidence_quality_insufficient
    )


class Cause(BaseModel):
    """One probable cause: an issue area, a description, and its confidence."""

    area: str
    description: str
    confidence_percent: int = Field(default=0, ge=0, le=100)
    confidence_label: ConfidenceLabel = ConfidenceLabel.low


class Verdict(BaseModel):
    """The schema-validated probable-cause verdict (#18).

    ``confidence_label`` is always derived from ``confidence_percent`` and the
    model refuses to hold the two out of sync. ``inconclusive`` is set by the
    correlation rule, not by the provider.
    """

    primary_cause: Cause
    alternatives: list[Cause] = Field(
        default_factory=list, max_length=MAX_ALTERNATIVES
    )
    confidence_percent: int = Field(ge=0, le=100)
    confidence_label: ConfidenceLabel
    severity: Severity
    rationale: str
    inconclusive: bool = False

    @model_validator(mode="after")
    def _label_matches_percent(self) -> Verdict:
        expected = confidence_label(self.confidence_percent)
        if self.confidence_label is not expected:
            raise ValueError(
                "confidence_label must match confidence_percent "
                f"({self.confidence_percent} -> {expected.value})"
            )
        return self


class EvidenceSummary(BaseModel):
    """One evidence source's credential-free summary, from the #12 inventory."""

    source_id: str
    issue_area: str
    status: SourceStatus
    title: str = ""
    note: str = ""

    @classmethod
    def from_source(cls, source: EvidenceSource) -> EvidenceSummary:
        """Build a summary from a #12 :class:`~app.evidence.models.EvidenceSource`."""
        return cls(
            source_id=source.query_id,
            issue_area=source.issue_area,
            status=source.status,
            title=source.title,
            note=source.note,
        )


class CorrelationRequest(BaseModel):
    """Everything the correlation is allowed to see: signals + evidence quality.

    Deliberately excludes credentials, raw rows, and previous investigations:
    the prompt is built from exactly these two collections (plan.md §5/§7).
    """

    investigation_id: int | None = None
    signals: list[Signal] = Field(default_factory=list)
    evidence_summaries: list[EvidenceSummary] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        signals: list[Signal],
        sources: list[EvidenceSource],
        *,
        investigation_id: int | None = None,
    ) -> CorrelationRequest:
        """Assemble a request from #17 signals and a #12 source inventory."""
        return cls(
            investigation_id=investigation_id,
            signals=list(signals),
            evidence_summaries=[EvidenceSummary.from_source(s) for s in sources],
        )
