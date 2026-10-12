"""Report payload schema (#19).

:class:`ReportPayload` is the single, shape-driving contract for the incident
report and dashboard: #20 renders it to PDF, #25 displays it, and #26 validates
synthetic data against it. It carries exactly the plan.md §8 requirements --
identity and scope, the sources that were checked, findings rolled up by issue
area (severity and confidence), the probable causes from #18, coverage gaps, and
read-only validation/remediation guidance.

The models are pure data: no credential field exists anywhere and every
free-text field that could echo server data is redacted at assembly time
(:mod:`app.report.assembly`). A required field has no default, so a payload
cannot be built with a plan.md §8 requirement silently missing -- construction
raises instead (the guardrail the issue asks for).

The #17 :class:`~app.rules.signals.Signal` and #18
:class:`~app.ai.schemas.Verdict`/:class:`~app.ai.schemas.Cause` types are reused
verbatim rather than duplicated, so #19 and its consumers cannot drift from the
layers that produced them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.ai.schemas import Cause, ConfidenceLabel, confidence_label
from app.evidence.models import SourceStatus
from app.rules.signals import Severity, Signal

#: The one read-only disclaimer every remediation block carries, shared with
#: #20/#25 so the report and the dashboard render the same sentence.
READ_ONLY_DISCLAIMER = "recommendation only, execute at the DBA's discretion"


class AreaSeverity(str, Enum):
    """Severity rolled up for one issue area.

    Extends the #17 high/medium/low :class:`~app.rules.signals.Severity` with
    ``none``: an area that was checked but produced no signal still appears in
    the report so the dashboard can show it was examined (issue #19 criterion).
    """

    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


class EvidenceItem(BaseModel):
    """One evidence row referenced by a finding, in report form."""

    evidence_id: str
    area: str
    timestamp: datetime | None = None
    description: str
    details: dict[str, Any] = Field(default_factory=dict)


class DataSourceReport(BaseModel):
    """One evidence source checked during the run, with its inventory status."""

    source_id: str
    title: str = ""
    category: str = ""
    issue_area: str = ""
    status: SourceStatus
    checked: bool
    note: str = ""


class CoverageGap(BaseModel):
    """A source that was not collected, carrying its exact inventory note."""

    source_id: str
    issue_area: str = ""
    status: SourceStatus
    note: str = ""


class AreaFindings(BaseModel):
    """Findings for one issue area, with the rolled-up severity and confidence.

    ``findings`` reuses the #17 :class:`~app.rules.signals.Signal` unchanged.
    ``severity`` is the maximum severity of the area's signals, or ``none`` when
    the area produced none; ``signal_count`` matches ``len(findings)``. The
    model refuses an inconsistent roll-up.
    """

    area: str
    severity: AreaSeverity
    confidence_percent: int = Field(default=0, ge=0, le=100)
    confidence_label: ConfidenceLabel = ConfidenceLabel.low
    signal_count: int = Field(default=0, ge=0)
    findings: list[Signal]
    evidence: list[EvidenceItem]

    @model_validator(mode="after")
    def _rollup_matches_findings(self) -> AreaFindings:
        if self.signal_count != len(self.findings):
            raise ValueError(
                f"signal_count {self.signal_count} does not match "
                f"{len(self.findings)} finding(s)"
            )
        if self.signal_count == 0 and self.severity is not AreaSeverity.none:
            raise ValueError("an area with no signals must have severity 'none'")
        if self.signal_count > 0 and self.severity is AreaSeverity.none:
            raise ValueError("an area with signals must not have severity 'none'")
        return self


class RemediationStep(BaseModel):
    """One remediation action for DBA review, with optional draft SQL.

    ``draft_sql`` is a *review* artifact only: the tool recommends, it never
    executes (plan.md §5/§6). ``disclaimer`` is always present so the report and
    dashboard can render the read-only notice consistently.
    """

    step: str
    draft_sql: str | None = None
    disclaimer: str = READ_ONLY_DISCLAIMER


class Recommendation(BaseModel):
    """Validation checks and remediation guidance for one area of the report.

    ``remediation`` is step-by-step guidance for a DBA to review and run at
    their discretion; it is never verbatim "the tool will run this" (plan.md
    §5/§6). Both the block and each step carry :data:`READ_ONLY_DISCLAIMER`.
    """

    area: str | None = None
    validation_checks: list[str]
    remediation: list[RemediationStep]
    disclaimer: str = READ_ONLY_DISCLAIMER


class InvestigationContext(BaseModel):
    """The identity/scope input :func:`app.report.assembly.assemble` reports on."""

    organization: str
    server_host: str
    server_port: int = 1433
    scope_kind: str = "instance"
    databases: list[str] = Field(default_factory=list)
    lookback_hours: int
    investigation_time: datetime
    investigation_id: int | None = None


class ReportPayload(BaseModel):
    """The complete, validated, read-only report payload (plan.md §8).

    Every plan.md §8 requirement is a required field with no default, so the
    model is the guarantee that assembly never silently omits one. ``Verdict``
    and ``Signal`` are embedded unchanged from #18/#17.
    """

    investigation_id: int | None = None

    organization: str
    server_host: str
    server_port: int
    database_scope: str
    databases: list[str]
    lookback_hours: int
    investigation_time: datetime

    data_sources: list[DataSourceReport]

    findings_by_area: list[AreaFindings]

    primary_cause: Cause
    alternatives: list[Cause]
    confidence_percent: int = Field(ge=0, le=100)
    confidence_label: ConfidenceLabel
    severity: Severity
    rationale: str
    inconclusive: bool

    coverage_gaps: list[CoverageGap]

    recommendations: list[Recommendation]
    validation_checks: list[str]
    remediation: list[RemediationStep]
    read_only_disclaimer: str = READ_ONLY_DISCLAIMER

    @model_validator(mode="after")
    def _verdict_consistent(self) -> ReportPayload:
        expected = confidence_label(self.confidence_percent)
        if self.confidence_label is not expected:
            raise ValueError(
                "confidence_label must match confidence_percent "
                f"({self.confidence_percent} -> {expected.value})"
            )
        if self.inconclusive:
            gap_ids = {gap.source_id for gap in self.coverage_gaps}
            missing = [
                source.source_id
                for source in self.data_sources
                if source.status is not SourceStatus.collected
                and source.source_id not in gap_ids
            ]
            if missing:
                raise ValueError(
                    "an inconclusive payload must carry a coverage gap for every "
                    f"source not collected; missing: {sorted(missing)}"
                )
        return self
