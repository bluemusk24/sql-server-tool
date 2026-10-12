"""Findings and report data assembly (#19).

:func:`assemble` is a pure data-in -> data-out function: given an
investigation's identity/scope, the #12 execution result, the #18 verdict, and
the read-only validation/remediation guidance, it returns one validated
:class:`~app.report.schemas.ReportPayload`. It never touches a database, a
network, or an LLM (the caller supplies the verdict; see :func:`build_report`
for the built-in, no-network correlation path).

Where each report field comes from:

* identity/scope/window <- the investigation context,
* data sources checked + their statuses <- the #12 run inventory,
* findings by area, severity, confidence and evidence <- the #13-#16
  collectors (driven here from the raw run rows) and the #17 rules engine,
* primary cause, alternatives, confidence, ``inconclusive`` <- the #18 verdict,
* coverage gaps <- every source whose inventory status is not ``collected``
  (their exact notes included),
* validation checks + remediation guidance <- the supplied ``Recommendation``
  objects, wired through unchanged.

All free text is redacted before it enters the payload and no credential can
reach it (the #12 run result is already credential-free; this module redacts the
exact key/value shapes the #5 helper knows). A required field is never defaulted
away: the :class:`ReportPayload` schema raises if one is missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.ai.client import correlate
from app.ai.correlation import SEVERITY_RANK
from app.ai.schemas import CorrelationRequest, Verdict, confidence_label
from app.diagnostics.catalog import Catalog
from app.evidence import availability as availability_evidence
from app.evidence import blocking as blocking_evidence
from app.evidence import deadlocks as deadlocks_evidence
from app.evidence import errors as errors_evidence
from app.evidence import jobs as jobs_evidence
from app.evidence import queries as queries_evidence
from app.evidence import storage as storage_evidence
from app.evidence.availability import collect_availability
from app.evidence.blocking import collect_blocking
from app.evidence.collectors import SourceEvidence
from app.evidence.deadlocks import collect_deadlocks
from app.evidence.errors import collect_errors
from app.evidence.jobs import collect_agent_jobs
from app.evidence.models import (
    DiagnosticRunResult,
    EvidenceRow,
    EvidenceSource,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.evidence.queries import collect_queries
from app.evidence.registry import EvidenceRegistry
from app.evidence.storage import collect_storage
from app.logredact import redact_text
from app.report.schemas import (
    AreaFindings,
    AreaSeverity,
    CoverageGap,
    DataSourceReport,
    EvidenceItem,
    InvestigationContext,
    Recommendation,
    RemediationStep,
    ReportPayload,
)
from app.rules.engine import evaluate, index_evidence
from app.rules.signals import ISSUE_AREAS, Signal

__all__ = [
    "assemble",
    "build_report",
    "collect_evidence",
    "collect_signals",
]

#: Issue areas in the canonical dashboard order (the #17 shared vocabulary).
AREA_ORDER = ISSUE_AREAS


def _coerce_investigation(investigation: object) -> InvestigationContext:
    """Normalize the several investigation shapes into :class:`InvestigationContext`.

    Accepts a context, a mapping, or any object exposing the #9 investigation
    fields (``organization``/``organization_id``, ``server_host``,
    ``server_port``, ``scope_kind``, ``databases``, ``lookback_hours``, and
    ``investigation_time``/``created_at``). A missing identity field raises
    rather than being silently blanked.
    """
    if isinstance(investigation, InvestigationContext):
        return investigation
    if isinstance(investigation, Mapping):
        return InvestigationContext.model_validate(dict(investigation))

    organization = getattr(investigation, "organization", None)
    if organization is None:
        organization_id = getattr(investigation, "organization_id", None)
        organization = str(organization_id) if organization_id is not None else None
    server_host = getattr(investigation, "server_host", None) or getattr(
        investigation, "server", None
    )
    investigation_time = getattr(
        investigation, "investigation_time", None
    ) or getattr(investigation, "created_at", None)
    if not organization or not server_host or investigation_time is None:
        raise ValueError(
            "the investigation must expose organization, server_host and an "
            "investigation time (investigation_time or created_at)"
        )
    return InvestigationContext(
        organization=str(organization),
        server_host=str(server_host),
        server_port=int(getattr(investigation, "server_port", 1433)),
        scope_kind=str(getattr(investigation, "scope_kind", "instance")),
        databases=list(getattr(investigation, "databases", None) or []),
        lookback_hours=int(getattr(investigation, "lookback_hours", 0)),
        investigation_time=investigation_time,
        investigation_id=getattr(investigation, "id", None),
    )


def _group_evidence(
    run_result: DiagnosticRunResult,
) -> dict[str, list[QueryEvidence]]:
    """Group the run's per-database raw evidence by catalog source id."""
    grouped: dict[str, list[QueryEvidence]] = {}
    for record in run_result.evidence:
        grouped.setdefault(record.query_id, []).append(record)
    return grouped


def _source_evidence(
    source_id: str,
    records: Sequence[QueryEvidence],
    source: EvidenceSource | None,
) -> SourceEvidence:
    """Build one collector input from every raw record for a source.

    Rows from all target databases are concatenated; the source is ``ok`` when
    any database succeeded, otherwise the first failure status is kept. The
    inventory status comes from the run result so a denied source stays
    ``unavailable-permission`` and is never treated as collected.
    """
    rows = [row for record in records for row in record.rows]
    if any(record.status is QueryStatus.ok for record in records):
        status = QueryStatus.ok
    elif records:
        status = records[0].status
    else:
        status = QueryStatus.skipped
    error = next((record.error for record in records if record.error), None)
    source_status = (
        source.status if source is not None else SourceStatus.not_applicable
    )
    return SourceEvidence(
        source_id=source_id,
        rows=rows,
        status=status,
        source_status=source_status,
        error=error,
        database=records[0].database if records else "",
    )


def _collect_evidence_and_registry(
    run_result: DiagnosticRunResult,
    *,
    databases: Sequence[str] | None = None,
) -> tuple[list[EvidenceRow], EvidenceRegistry]:
    """Run every #13-#16 collector over the run result (no SQL, no I/O)."""
    registry = EvidenceRegistry(Catalog())
    by_source = _group_evidence(run_result)
    by_id = {source.query_id: source for source in run_result.sources}

    def source_evidence(source_id: str) -> SourceEvidence:
        return _source_evidence(
            source_id, by_source.get(source_id, []), by_id.get(source_id)
        )

    rows: list[EvidenceRow] = []
    rows += collect_blocking(
        source_evidence(blocking_evidence.CURRENT_SOURCE_ID), registry
    )
    rows += collect_deadlocks(source_evidence(deadlocks_evidence.SOURCE_ID), registry)
    rows += collect_queries(source_evidence(queries_evidence.SOURCE_ID), registry)
    rows += collect_agent_jobs(source_evidence(jobs_evidence.SOURCE_ID), registry)
    rows += collect_errors(source_evidence(errors_evidence.SOURCE_ID), registry)
    rows += collect_availability(
        source_evidence(availability_evidence.SOURCE_ID),
        registry,
        databases=databases,
    )
    rows += collect_storage(
        source_evidence(storage_evidence.SOURCE_ID),
        registry,
        databases=databases,
    )
    return rows, registry


def collect_evidence(
    run_result: DiagnosticRunResult,
    *,
    databases: Sequence[str] | None = None,
) -> list[EvidenceRow]:
    """Turn a #12 run result into the normalized rows the #17 rules consume."""
    rows, _registry = _collect_evidence_and_registry(run_result, databases=databases)
    return rows


def collect_signals(
    run_result: DiagnosticRunResult,
    *,
    databases: Sequence[str] | None = None,
) -> list[Signal]:
    """Evaluate the run result's evidence into #17 signals."""
    rows, _registry = _collect_evidence_and_registry(run_result, databases=databases)
    return evaluate(rows, run_result.sources)


def _rollup_severity(signals: Sequence[Signal]) -> AreaSeverity:
    """The maximum severity across ``signals``, or ``none`` when there are none."""
    if not signals:
        return AreaSeverity.none
    strongest = max(signals, key=lambda signal: SEVERITY_RANK[signal.severity])
    return AreaSeverity(strongest.severity.value)


def _redact_value(value: Any) -> Any:
    """Recursively redact string values inside a details mapping."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _redact_signal(signal: Signal) -> Signal:
    """Return a copy of ``signal`` with its description redacted."""
    return signal.model_copy(update={"description": redact_text(signal.description)})


def _redact_cause(cause: Any) -> Any:
    """Return a copy of a #18 cause with its description redacted."""
    return cause.model_copy(update={"description": redact_text(cause.description)})


def _evidence_item(evidence_id: str, row: EvidenceRow) -> EvidenceItem:
    """Map an emitted evidence id + raw row onto a report evidence item."""
    return EvidenceItem(
        evidence_id=evidence_id,
        area=row.area,
        timestamp=row.timestamp,
        description=redact_text(row.description),
        details=_redact_value(row.details),
    )


def _findings_by_area(
    signals: Sequence[Signal],
    indexed: Mapping[str, EvidenceRow],
    verdict: Verdict,
) -> list[AreaFindings]:
    """Roll signals up into one entry per issue area, in canonical order.

    Every area appears -- even one with no signals (``severity: "none"``,
    ``signal_count: 0``) -- so the dashboard can show it was checked. The
    per-area confidence is the confidence the verdict assigned to that area as a
    probable cause (primary or alternative), or low/0 when the area is not a
    named cause.
    """
    groups: dict[str, list[Signal]] = {}
    for signal in signals:
        groups.setdefault(signal.area, []).append(signal)

    cause_confidence: dict[str, int] = {
        verdict.primary_cause.area: verdict.primary_cause.confidence_percent
    }
    for alternative in verdict.alternatives:
        cause_confidence.setdefault(alternative.area, alternative.confidence_percent)

    areas = list(AREA_ORDER)
    for area in groups:
        if area not in areas:
            areas.append(area)

    findings_by_area: list[AreaFindings] = []
    for area in areas:
        area_signals = groups.get(area, [])
        evidence: list[EvidenceItem] = []
        seen: set[str] = set()
        for signal in area_signals:
            for evidence_id in signal.evidence_ids:
                if evidence_id in seen:
                    continue
                row = indexed.get(evidence_id)
                if row is None:
                    continue
                seen.add(evidence_id)
                evidence.append(_evidence_item(evidence_id, row))
        percent = cause_confidence.get(area, 0)
        findings_by_area.append(
            AreaFindings(
                area=area,
                severity=_rollup_severity(area_signals),
                confidence_percent=percent,
                confidence_label=confidence_label(percent),
                signal_count=len(area_signals),
                findings=[_redact_signal(signal) for signal in area_signals],
                evidence=evidence,
            )
        )
    return findings_by_area


def _data_sources(run_result: DiagnosticRunResult) -> list[DataSourceReport]:
    """Report every checked source and its inventory status."""
    return [
        DataSourceReport(
            source_id=source.query_id,
            title=source.title,
            category=source.category,
            issue_area=source.issue_area,
            status=source.status,
            checked=source.status is SourceStatus.collected,
            note=redact_text(source.note),
        )
        for source in run_result.sources
    ]


def _coverage_gaps(
    run_result: DiagnosticRunResult, registry: EvidenceRegistry
) -> list[CoverageGap]:
    """Every source not ``collected``, with its exact note.

    The run inventory is authoritative; synthetic gap sources the collectors
    emit (for example historical blocking, which has no catalog query) are added
    when the run inventory does not already carry them.
    """
    gaps: list[CoverageGap] = []
    seen: set[str] = set()
    for source in run_result.sources:
        seen.add(source.query_id)
        if source.status is not SourceStatus.collected:
            gaps.append(
                CoverageGap(
                    source_id=source.query_id,
                    issue_area=source.issue_area,
                    status=source.status,
                    note=redact_text(source.note),
                )
            )
    for source in registry.all():
        if source.query_id in seen or source.status is SourceStatus.collected:
            continue
        gaps.append(
            CoverageGap(
                source_id=source.query_id,
                issue_area=source.issue_area,
                status=source.status,
                note=redact_text(source.note),
            )
        )
    return gaps


def _redact_recommendation(recommendation: Recommendation) -> Recommendation:
    """Return a copy with every free-text field redacted.

    Validation checks, remediation steps and draft SQL are DBA-facing prose but
    are still redacted for the same reason as the rest of the payload: a
    secret-shaped value must never reach the report (or the PDF in #20).
    """
    return recommendation.model_copy(
        update={
            "validation_checks": [
                redact_text(check) for check in recommendation.validation_checks
            ],
            "remediation": [
                step.model_copy(
                    update={
                        "step": redact_text(step.step),
                        "draft_sql": (
                            redact_text(step.draft_sql)
                            if step.draft_sql is not None
                            else None
                        ),
                        "disclaimer": redact_text(step.disclaimer),
                    }
                )
                for step in recommendation.remediation
            ],
            "disclaimer": redact_text(recommendation.disclaimer),
        }
    )


def _normalize_recommendations(
    recommendations: Sequence[Recommendation]
    | Recommendation
    | Mapping[str, Any]
    | None,
) -> list[Recommendation]:
    """Normalize the ``recommendations`` argument into redacted models."""
    if recommendations is None:
        return []
    if isinstance(recommendations, Recommendation):
        return [_redact_recommendation(recommendations)]
    if isinstance(recommendations, Mapping):
        return [
            _redact_recommendation(
                Recommendation.model_validate(dict(recommendations))
            )
        ]
    normalized: list[Recommendation] = []
    for item in recommendations:
        if isinstance(item, Recommendation):
            normalized.append(_redact_recommendation(item))
        else:
            normalized.append(
                _redact_recommendation(Recommendation.model_validate(item))
            )
    return normalized


def assemble(
    investigation: InvestigationContext | object,
    run_result: DiagnosticRunResult,
    verdict: Verdict,
    recommendations: Sequence[Recommendation]
    | Recommendation
    | Mapping[str, Any]
    | None = None,
) -> ReportPayload:
    """Assemble one complete, validated, read-only report payload.

    Pure and deterministic for identical inputs. The verdict is supplied by the
    caller (see :func:`build_report`); assembly itself never calls an LLM. A
    required plan.md §8 field cannot be omitted: the payload model raises.
    """
    context = _coerce_investigation(investigation)
    databases = context.databases or None

    evidence_rows, registry = _collect_evidence_and_registry(
        run_result, databases=databases
    )
    signals = evaluate(evidence_rows, run_result.sources)
    indexed = {item.evidence_id: item.row for item in index_evidence(evidence_rows)}

    normalized_recommendations = _normalize_recommendations(recommendations)
    validation_checks = [
        check
        for recommendation in normalized_recommendations
        for check in recommendation.validation_checks
    ]
    remediation: list[RemediationStep] = [
        step
        for recommendation in normalized_recommendations
        for step in recommendation.remediation
    ]

    return ReportPayload(
        investigation_id=context.investigation_id,
        organization=redact_text(context.organization),
        server_host=context.server_host,
        server_port=context.server_port,
        database_scope=context.scope_kind,
        databases=list(context.databases),
        lookback_hours=context.lookback_hours,
        investigation_time=context.investigation_time,
        data_sources=_data_sources(run_result),
        findings_by_area=_findings_by_area(signals, indexed, verdict),
        primary_cause=_redact_cause(verdict.primary_cause),
        alternatives=[_redact_cause(cause) for cause in verdict.alternatives],
        confidence_percent=verdict.confidence_percent,
        confidence_label=verdict.confidence_label,
        severity=verdict.severity,
        rationale=redact_text(verdict.rationale),
        inconclusive=verdict.inconclusive,
        coverage_gaps=_coverage_gaps(run_result, registry),
        recommendations=normalized_recommendations,
        validation_checks=validation_checks,
        remediation=remediation,
    )


def build_report(
    investigation: InvestigationContext | object,
    run_result: DiagnosticRunResult,
    recommendations: Sequence[Recommendation]
    | Recommendation
    | Mapping[str, Any]
    | None = None,
    *,
    provider: Any | None = None,
) -> ReportPayload:
    """Compute signals + verdict, then :func:`assemble` a report.

    This is the convenience path for callers that do not already hold a #18
    verdict. With no ``provider`` it uses the built-in, deterministic
    correlation heuristics -- no network call, no external LLM. An injected
    provider is passed straight to :func:`app.ai.client.correlate`.
    """
    context = _coerce_investigation(investigation)
    signals = collect_signals(run_result, databases=context.databases or None)
    request = CorrelationRequest.build(
        signals,
        run_result.sources,
        investigation_id=context.investigation_id,
    )
    verdict = correlate(request, provider=provider)
    return assemble(context, run_result, verdict, recommendations)
