"""Unit tests for #19 findings and report data assembly.

Everything runs with no SQL Server and no network: the assembler is a pure
function of a #12 run result, a #18 verdict and the supplied recommendations,
and the built-in correlation path is used when a verdict is not supplied.

Covered: findings derived from rules + correlation, the severity roll-up,
zero-signal areas still appearing, coverage gaps with exact notes, the
inconclusive case, empty/minimal inputs, determinism, redaction, the read-only
recommendation contract, the fixture-driven schema completeness check and its
negative counterpart, and wire-through of the #17/#18 types.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.schemas import Cause, ConfidenceLabel, Verdict
from app.evidence.models import (
    DiagnosticRunResult,
    EvidenceSource,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.investigations.repository import InMemoryInvestigationRepository
from app.report import (
    READ_ONLY_DISCLAIMER,
    AreaSeverity,
    InvestigationContext,
    Recommendation,
    RemediationStep,
    ReportPayload,
    assemble,
    build_report,
)
from app.report.assembly import AREA_ORDER
from app.rules.signals import Severity, Signal

FIXTURES = Path(__file__).parent / "fixtures" / "report"
COMPLETED = SourceStatus.collected
PERMISSION = SourceStatus.unavailable_permission
NOT_APPLICABLE = SourceStatus.not_applicable


# --- fixture loading --------------------------------------------------------


def _fixture() -> tuple[
    InvestigationContext, DiagnosticRunResult, Verdict, list[Recommendation]
]:
    data = json.loads((FIXTURES / "assemble_input.json").read_text(encoding="utf-8"))
    context = InvestigationContext.model_validate(data["investigation"])
    run_result = DiagnosticRunResult(
        investigation_id=context.investigation_id or 0,
        sources=[EvidenceSource.model_validate(s) for s in data["sources"]],
        evidence=[QueryEvidence.model_validate(e) for e in data["evidence"]],
    )
    verdict = Verdict.model_validate(data["verdict"])
    recommendations = [
        Recommendation.model_validate(r) for r in data["recommendations"]
    ]
    return context, run_result, verdict, recommendations


def _context() -> InvestigationContext:
    return InvestigationContext(
        organization="Acme Corp",
        server_host="sql01.acme.example",
        server_port=1433,
        scope_kind="instance",
        databases=[],
        lookback_hours=4,
        investigation_time=datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        investigation_id=1,
    )


def _source(
    query_id: str, area: str, status: SourceStatus = COMPLETED, note: str = ""
) -> EvidenceSource:
    return EvidenceSource(
        query_id=query_id,
        title=f"{area} source",
        category=area,
        issue_area=area,
        status=status,
        note=note,
    )


def _run(sources: list[EvidenceSource], evidence: list[QueryEvidence]):
    return DiagnosticRunResult(investigation_id=1, sources=sources, evidence=evidence)


def _verdict(
    *,
    area: str = "blocking",
    percent: int = 82,
    inconclusive: bool = False,
    alternatives: list[Cause] | None = None,
) -> Verdict:
    from app.ai.schemas import confidence_label

    return Verdict(
        primary_cause=Cause(
            area=area,
            description="probable cause",
            confidence_percent=percent,
            confidence_label=confidence_label(percent),
        ),
        alternatives=alternatives or [],
        confidence_percent=percent,
        confidence_label=confidence_label(percent),
        severity=Severity.high,
        rationale="rationale",
        inconclusive=inconclusive,
    )


# --- findings from rules + correlation --------------------------------------


def test_findings_come_from_the_rules_engine_and_carry_evidence():
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    areas = {entry.area: entry for entry in payload.findings_by_area}
    assert areas["blocking"].severity is AreaSeverity.high
    assert areas["blocking"].signal_count == 1
    assert areas["blocking"].findings[0].area == "blocking"
    assert areas["blocking"].evidence
    assert areas["blocking"].evidence[0].description
    assert areas["errors"].severity is AreaSeverity.high
    assert areas["agent_jobs"].severity is AreaSeverity.medium
    assert areas["queries"].severity is AreaSeverity.high
    assert areas["storage"].severity is AreaSeverity.high
    # Confidence on a cause area comes from the verdict's matching cause.
    assert areas["blocking"].confidence_percent == 82
    assert areas["errors"].confidence_percent == 67


def test_zero_signal_areas_still_appear_with_severity_none_and_count_zero():
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    areas = {entry.area: entry for entry in payload.findings_by_area}
    assert set(areas) == set(AREA_ORDER)
    for area in ("deadlocks", "cpu", "availability"):
        assert areas[area].severity is AreaSeverity.none
        assert areas[area].signal_count == 0
        assert areas[area].findings == []
        assert areas[area].confidence_label is ConfidenceLabel.low


def test_area_severity_is_the_maximum_of_its_signals():
    sources = [_source("blocking_current_chains", "blocking")]
    evidence = [
        QueryEvidence(
            query_id="blocking_current_chains",
            database="master",
            status=QueryStatus.ok,
            rows=[
                {"session_id": 1, "blocking_session_id": 2, "wait_time": 6_000},
                {"session_id": 3, "blocking_session_id": 4, "wait_time": 45_000},
            ],
        )
    ]

    payload = assemble(_context(), _run(sources, evidence), _verdict())

    blocking = next(a for a in payload.findings_by_area if a.area == "blocking")
    assert blocking.signal_count == 2
    assert {signal.severity for signal in blocking.findings} == {
        Severity.medium,
        Severity.high,
    }
    assert blocking.severity is AreaSeverity.high


def test_signals_and_verdict_types_are_reused_not_duplicated():
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    blocking = next(a for a in payload.findings_by_area if a.area == "blocking")
    assert isinstance(blocking.findings[0], Signal)
    assert payload.primary_cause is not None
    assert payload.primary_cause.area == verdict.primary_cause.area
    assert payload.confidence_percent == verdict.confidence_percent


# --- coverage gaps / inconclusive -------------------------------------------


def test_inconclusive_payload_carries_every_uncollected_source_with_exact_notes():
    context, run_result, verdict, recommendations = _fixture()
    inconclusive = verdict.model_copy(
        update={
            "inconclusive": True,
            "confidence_percent": 0,
            "confidence_label": ConfidenceLabel.low,
            "primary_cause": Cause(
                area="unknown",
                description="insufficient evidence",
                confidence_percent=0,
                confidence_label=ConfidenceLabel.low,
            ),
        }
    )

    payload = assemble(context, run_result, inconclusive, recommendations)

    assert payload.inconclusive is True
    gaps = {gap.source_id: gap for gap in payload.coverage_gaps}
    assert gaps["non_online_databases"].status is PERMISSION
    assert (
        gaps["non_online_databases"].note
        == "The SELECT permission was denied on the object 'sys.databases'."
    )
    assert gaps["deadlock_system_health"].status is NOT_APPLICABLE
    assert (
        gaps["deadlock_system_health"].note
        == "server retained no system_health ring buffer evidence"
    )
    uncollected = {
        source.source_id
        for source in payload.data_sources
        if source.status is not COMPLETED
    }
    assert uncollected <= set(gaps)


def test_coverage_gaps_are_present_even_when_the_verdict_is_confident():
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    assert payload.inconclusive is False
    assert payload.coverage_gaps
    gap_ids = {gap.source_id for gap in payload.coverage_gaps}
    for source in payload.data_sources:
        if source.status is not COMPLETED:
            assert source.source_id in gap_ids


# --- empty / minimal cases --------------------------------------------------


def test_empty_run_result_is_valid_and_reports_only_checked_areas():
    empty = _run([], [])

    payload = assemble(_context(), empty, _verdict(inconclusive=True, percent=0), [])

    assert payload.data_sources == []
    assert all(
        entry.severity is AreaSeverity.none and entry.signal_count == 0
        for entry in payload.findings_by_area
    )
    assert payload.validation_checks == []
    assert payload.remediation == []
    assert payload.recommendations == []
    # Historical blocking has no catalog source, so it surfaces as a gap only.
    assert "blocking_history" in {gap.source_id for gap in payload.coverage_gaps}
    assert all(
        gap.status is not COMPLETED for gap in payload.coverage_gaps
    )


def test_minimal_run_with_one_collected_source_has_no_signals_for_a_clean_area():
    sources = [_source("blocking_current_chains", "blocking")]
    evidence = [
        QueryEvidence(
            query_id="blocking_current_chains",
            database="master",
            status=QueryStatus.ok,
            rows=[],
        )
    ]

    payload = assemble(_context(), _run(sources, evidence), _verdict(percent=0), [])

    blocking = next(a for a in payload.findings_by_area if a.area == "blocking")
    assert blocking.severity is AreaSeverity.none
    assert blocking.signal_count == 0


# --- determinism and purity -------------------------------------------------


def test_assemble_is_deterministic():
    context, run_result, verdict, recommendations = _fixture()

    first = assemble(context, run_result, verdict, recommendations).model_dump(
        mode="json"
    )
    second = assemble(context, run_result, verdict, recommendations).model_dump(
        mode="json"
    )

    assert first == second


def test_assemble_makes_no_db_or_network_call(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("assembly must be pure: no DB or network")

    monkeypatch.setattr("app.sql.connector.connect", _boom)
    monkeypatch.setattr("app.ai.providers.httpx.Client", _boom)
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    assert isinstance(payload, ReportPayload)


def test_build_report_uses_builtin_correlation_without_network(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the built-in correlation path must not use httpx")

    monkeypatch.setattr("app.ai.providers.httpx.Client", _boom)
    _, run_result, _, recommendations = _fixture()

    payload = build_report(_context(), run_result, recommendations)

    assert isinstance(payload, ReportPayload)
    assert payload.primary_cause.area == "blocking"
    assert payload.inconclusive is False
    assert payload.confidence_label is ConfidenceLabel.high


# --- redaction --------------------------------------------------------------


def test_redaction_is_enforced_across_the_assembled_payload():
    secret = "s3cret-P@ssw0rd"
    sources = [
        _source(
            "sql_server_error_log",
            "errors",
            status=SourceStatus.unavailable_setting,
            note=f"login failed password={secret}",
        )
    ]
    evidence = [
        QueryEvidence(
            query_id="sql_server_error_log",
            database="master",
            status=QueryStatus.error,
            error=f"login failed password={secret}",
        )
    ]
    verdict = _verdict(area="errors", percent=40, inconclusive=True).model_copy(
        update={"rationale": f"correlation blocked by password={secret}"}
    )
    recommendations = [
        Recommendation(
            area="errors",
            validation_checks=[f"verify password={secret} is out of the URL"],
            remediation=[
                RemediationStep(
                    step=f"rotate password={secret}",
                    draft_sql=f"SELECT 'password={secret}';",
                )
            ],
        )
    ]

    payload = assemble(_context(), _run(sources, evidence), verdict, recommendations)
    rendered = json.dumps(payload.model_dump(mode="json"))

    assert secret not in rendered
    assert "****" in rendered
    assert rendered.count(secret) == 0


# --- recommendations contract ----------------------------------------------


def test_recommendation_contract_and_read_only_disclaimer():
    context, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    recommendation = payload.recommendations[0]
    assert recommendation.validation_checks
    assert all(isinstance(check, str) for check in recommendation.validation_checks)
    assert recommendation.remediation
    assert payload.validation_checks == recommendation.validation_checks
    assert payload.remediation[0].draft_sql is not None
    assert payload.recommendations[0].disclaimer == READ_ONLY_DISCLAIMER
    assert payload.read_only_disclaimer == READ_ONLY_DISCLAIMER
    assert all(
        step.disclaimer == READ_ONLY_DISCLAIMER for step in payload.remediation
    )
    rendered = json.dumps(payload.model_dump(mode="json")).lower()
    assert "the tool will run" not in rendered


# --- schema completeness / negative fixtures --------------------------------


def test_json_dump_matches_the_committed_section8_schema():
    context, run_result, verdict, recommendations = _fixture()

    dump = assemble(context, run_result, verdict, recommendations).model_dump(
        mode="json"
    )
    schema = json.loads((FIXTURES / "report_schema.json").read_text(encoding="utf-8"))

    assert set(dump) == set(schema["properties"])
    assert {name for name, field in ReportPayload.model_fields.items() if field.is_required()} == set(
        schema["required"]
    )


def test_negative_fixture_missing_a_required_field_is_rejected():
    bad = json.loads((FIXTURES / "missing_field.json").read_text(encoding="utf-8"))

    assert "data_sources" not in bad
    with pytest.raises(ValidationError):
        ReportPayload.model_validate(bad)


def test_area_findings_reject_an_inconsistent_rollup():
    with pytest.raises(ValidationError):
        from app.report import AreaFindings

        AreaFindings(
            area="blocking",
            severity=AreaSeverity.none,
            signal_count=1,
            findings=[
                Signal(
                    area="blocking",
                    severity=Severity.high,
                    description="blocking",
                    evidence_ids=["e1"],
                )
            ],
            evidence=[],
        )


# --- investigation input shapes ---------------------------------------------


def test_missing_investigation_identity_raises():
    _, run_result, verdict, recommendations = _fixture()

    with pytest.raises(ValueError):
        assemble(object(), run_result, verdict, recommendations)


def test_investigation_record_is_accepted():
    repository = InMemoryInvestigationRepository()
    record = repository.create_investigation(
        organization_id=7,
        server_host="sql01",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=4,
        status="succeeded",
    )
    _, run_result, verdict, recommendations = _fixture()

    payload = assemble(record, run_result, verdict, recommendations)

    assert payload.organization == "7"
    assert payload.investigation_id == record.id
    assert payload.investigation_time == record.created_at


def test_database_scoped_investigation_passes_its_scope_along():
    context = _context().model_copy(
        update={"scope_kind": "databases", "databases": ["App", "Sales"]}
    )
    _c, run_result, verdict, recommendations = _fixture()

    payload = assemble(context, run_result, verdict, recommendations)

    assert payload.database_scope == "databases"
    assert payload.databases == ["App", "Sales"]
