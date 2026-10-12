"""Built-in, deterministic correlation heuristics (#18).

This is the no-network path: given a :class:`~app.ai.schemas.CorrelationRequest`
it scores the #17 signals by severity, groups them by issue area, and derives a
:class:`~app.ai.schemas.Verdict` without calling any external service. The same
input always yields the same verdict (stable ordering, no randomness, no clocks),
so it is safe to unit-test and to run in offline/synthetic evaluation.

The scoring is intentionally simple and explainable -- a DBA can read exactly
why an area ranked first. It is a local fallback/alternative to an injected
provider; it never sees credentials.
"""

from __future__ import annotations

from app.ai.schemas import (
    CONFIDENCE_LOW_MAX,
    INSUFFICIENT_EVIDENCE_DESCRIPTION,
    MAX_ALTERNATIVES,
    Cause,
    ConfidenceLabel,
    CorrelationRequest,
    EvidenceSummary,
    Verdict,
    confidence_label,
    is_inconclusive,
)
from app.evidence.models import SourceStatus
from app.rules.engine import AREA_ORDER
from app.rules.signals import Severity, Signal

#: Weight a single signal contributes to its area's score, by severity.
SEVERITY_WEIGHT: dict[Severity, int] = {
    Severity.high: 3,
    Severity.medium: 2,
    Severity.low: 1,
}

#: Ordinal used to pick the strongest severity deterministically.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.high: 3,
    Severity.medium: 2,
    Severity.low: 1,
}


def evidence_quality_insufficient(summaries: list[EvidenceSummary]) -> bool:
    """Whether the evidence is too thin to conclude anything.

    True when there are no sources at all, or when not a single source was
    ``collected`` -- e.g. every major source is ``unavailable-*`` (plan.md §5).
    A partially-collected run is *sufficient*: plan.md §3.8 keeps partial
    findings rather than declaring the whole result inconclusive.
    """
    if not summaries:
        return True
    return not any(summary.status is SourceStatus.collected for summary in summaries)


def _area_rank(area: str) -> int:
    """Stable sort key: the fixed #17 area order, unknown areas last."""
    return AREA_ORDER.index(area) if area in AREA_ORDER else len(AREA_ORDER)


def _strongest(signals: list[Signal]) -> Signal:
    """The highest-severity signal, first-seen wins ties (deterministic)."""
    return max(signals, key=lambda signal: SEVERITY_RANK[signal.severity])


def _severity_of(signals: list[Signal]) -> Severity:
    """The strongest severity across ``signals``."""
    return _strongest(signals).severity


def _heuristic_confidence(
    groups: dict[str, list[Signal]], signals: list[Signal]
) -> int:
    """A bounded confidence from breadth (areas), depth (high signals), and count."""
    distinct_areas = len(groups)
    high_signals = sum(1 for s in signals if s.severity is Severity.high)
    raw = (
        30
        + min(distinct_areas, 4) * 10
        + min(high_signals, 3) * 8
        + min(len(signals), 6) * 2
    )
    return max(0, min(95, raw))


def _cause(area: str, signals: list[Signal], percent: int) -> Cause:
    """A cause for ``area`` whose description is its strongest signal's."""
    return Cause(
        area=area,
        description=_strongest(signals).description,
        confidence_percent=percent,
        confidence_label=confidence_label(percent),
    )


def apply_inconclusive_rule(verdict: Verdict, request: CorrelationRequest) -> Verdict:
    """Mark ``verdict`` inconclusive and reshape it when required.

    Applies the one shared rule: confidence below 50% **or** insufficient
    evidence quality. When inconclusive, the primary cause becomes
    "insufficient evidence", confidence drops into the low band, and the
    alternatives are kept but shown at low confidence (plan.md §5).
    """
    insufficient = evidence_quality_insufficient(request.evidence_summaries)
    if not is_inconclusive(verdict.confidence_percent, insufficient):
        return verdict

    percent = min(verdict.confidence_percent, CONFIDENCE_LOW_MAX)
    primary = Cause(
        area=verdict.primary_cause.area or "unknown",
        description=INSUFFICIENT_EVIDENCE_DESCRIPTION,
        confidence_percent=percent,
        confidence_label=ConfidenceLabel.low,
    )
    alternatives = [
        alternative.model_copy(
            update={
                "confidence_percent": min(
                    alternative.confidence_percent, CONFIDENCE_LOW_MAX
                ),
                "confidence_label": ConfidenceLabel.low,
            }
        )
        for alternative in verdict.alternatives
    ]
    return verdict.model_copy(
        update={
            "primary_cause": primary,
            "alternatives": alternatives,
            "confidence_percent": percent,
            "confidence_label": ConfidenceLabel.low,
            "inconclusive": True,
            "rationale": (
                f"{INSUFFICIENT_EVIDENCE_DESCRIPTION.capitalize()}: {verdict.rationale}"
            ),
        }
    )


def heuristic_verdict(request: CorrelationRequest) -> Verdict:
    """Derive a verdict from the request with no provider and no I/O.

    Deterministic: identical requests yield identical verdicts. An empty signal
    set yields an inconclusive, zero-confidence verdict rather than an error.
    """
    signals = list(request.signals)
    if not signals:
        verdict = Verdict(
            primary_cause=Cause(
                area="unknown",
                description=INSUFFICIENT_EVIDENCE_DESCRIPTION,
                confidence_percent=0,
                confidence_label=ConfidenceLabel.low,
            ),
            alternatives=[],
            confidence_percent=0,
            confidence_label=ConfidenceLabel.low,
            severity=Severity.low,
            rationale="No rule signals were produced for this investigation.",
        )
        return apply_inconclusive_rule(verdict, request)

    groups: dict[str, list[Signal]] = {}
    for signal in signals:
        groups.setdefault(signal.area, []).append(signal)

    scores = {
        area: sum(SEVERITY_WEIGHT[signal.severity] for signal in items)
        for area, items in groups.items()
    }
    ordered = sorted(groups, key=lambda area: (-scores[area], _area_rank(area)))

    percent = _heuristic_confidence(groups, signals)
    primary_area = ordered[0]
    alternatives = [
        _cause(area, groups[area], max(0, percent - 15 - index * 10))
        for index, area in enumerate(ordered[1 : MAX_ALTERNATIVES + 1])
    ]
    rationale = (
        f"{len(signals)} signal(s) across {len(groups)} issue area(s); "
        f"'{primary_area}' scored highest."
    )
    verdict = Verdict(
        primary_cause=_cause(primary_area, groups[primary_area], percent),
        alternatives=alternatives,
        confidence_percent=percent,
        confidence_label=confidence_label(percent),
        severity=_severity_of(signals),
        rationale=rationale,
    )
    return apply_inconclusive_rule(verdict, request)
