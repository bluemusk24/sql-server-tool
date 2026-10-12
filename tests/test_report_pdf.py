"""Tests for #20 report PDF export.

Everything runs with no SQL Server and no network. The PDF tests use WeasyPrint
against the committed ``app/report/templates/report.html`` template; if
WeasyPrint or its system libraries (Pango/HarfBuzz/fontconfig) are missing, the
PDF-producing tests skip visibly with the missing-library reason (the
``WEASYPRINT_AVAILABLE`` flag exposes it), and the rest of the suite still runs.

Covered: the fixture payload (a >= 5-line draft-SQL block and a >= 2000-char
evidence string) renders to bytes starting with ``%PDF-`` that pypdf can parse;
the template renders every plan.md §8 section; the download endpoint returns the
right headers and bytes, 404s for a missing report and 500s on a failed render
without leaking a traceback; empty findings still render; redaction removes
secret-shaped values; and the document body is deterministic.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.schemas import Cause, ConfidenceLabel
from app.report.pdf import (
    WEASYPRINT_AVAILABLE,
    WEASYPRINT_ERROR,
    PdfRenderError,
    render,
    render_html,
    report_filename,
)
from app.report.repository import InMemoryReportRepository
from app.report.schemas import (
    READ_ONLY_DISCLAIMER,
    AreaFindings,
    AreaSeverity,
    CoverageGap,
    DataSourceReport,
    EvidenceItem,
    Recommendation,
    RemediationStep,
    ReportPayload,
)
from app.rules.signals import Severity, Signal

FIXTURES = Path(__file__).parent / "fixtures" / "report"

LONG_EVIDENCE = "Blocked session detail: " + ("lock-wait evidence " * 140)  # > 2000 chars
DRAFT_SQL = (
    "SELECT session_id,\n"
    "       blocking_session_id,\n"
    "       wait_type,\n"
    "       wait_time\n"
    "FROM sys.dm_exec_requests\n"
    "WHERE blocking_session_id <> 0;"
)

needs_weasyprint = pytest.mark.skipif(
    not WEASYPRINT_AVAILABLE,
    reason=(
        "WeasyPrint/system libraries unavailable"
        + (f": {WEASYPRINT_ERROR}" if WEASYPRINT_ERROR else "")
    ),
)


# --- fixtures ---------------------------------------------------------------


def _payload(**overrides) -> ReportPayload:
    """A complete fixture payload with a long SQL block and long evidence."""
    blocking_evidence = EvidenceItem(
        evidence_id="ev-blocking-1",
        area="blocking",
        timestamp=datetime(2026, 10, 10, 9, 59, tzinfo=UTC),
        description=LONG_EVIDENCE,
    )
    data = {
        "investigation_id": 42,
        "organization": "Acme Corp",
        "server_host": "sql01.acme.example",
        "server_port": 1433,
        "database_scope": "instance",
        "databases": [],
        "lookback_hours": 4,
        "investigation_time": datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        "data_sources": [
            DataSourceReport(
                source_id="blocking_current_chains",
                title="Current blocking chains",
                category="blocking",
                issue_area="blocking",
                status="collected",
                checked=True,
                note="1 currently blocked session(s) found.",
            ),
            DataSourceReport(
                source_id="non_online_databases",
                title="Non-online databases",
                category="availability",
                issue_area="availability",
                status="unavailable-permission",
                checked=False,
                note="The SELECT permission was denied on sys.databases.",
            ),
        ],
        "findings_by_area": [
            AreaFindings(
                area="blocking",
                severity=AreaSeverity.high,
                confidence_percent=82,
                confidence_label=ConfidenceLabel.high,
                signal_count=1,
                findings=[
                    Signal(
                        area="blocking",
                        severity=Severity.high,
                        description="Session 60 blocked by session 57 for 45000 ms.",
                        evidence_ids=["ev-blocking-1"],
                    )
                ],
                evidence=[blocking_evidence],
            ),
            AreaFindings(
                area="deadlocks",
                severity=AreaSeverity.none,
                confidence_percent=0,
                confidence_label=ConfidenceLabel.low,
                signal_count=0,
                findings=[],
                evidence=[],
            ),
        ],
        "primary_cause": Cause(
            area="blocking",
            description="Session 60 is blocked by session 57.",
            confidence_percent=82,
            confidence_label=ConfidenceLabel.high,
        ),
        "alternatives": [
            Cause(
                area="storage",
                description="Database 'App' has 5.00% free space.",
                confidence_percent=57,
                confidence_label=ConfidenceLabel.medium,
            )
        ],
        "confidence_percent": 82,
        "confidence_label": ConfidenceLabel.high,
        "severity": Severity.high,
        "rationale": "blocking scored highest across the checked evidence.",
        "inconclusive": False,
        "coverage_gaps": [
            CoverageGap(
                source_id="non_online_databases",
                issue_area="availability",
                status="unavailable-permission",
                note="The SELECT permission was denied on sys.databases.",
            )
        ],
        "recommendations": [
            Recommendation(
                area="blocking",
                validation_checks=["Confirm session 57 still holds locks."],
                remediation=[
                    RemediationStep(
                        step="Review the blocking query with the app owner.",
                        draft_sql=DRAFT_SQL,
                    )
                ],
            )
        ],
        "validation_checks": ["Confirm session 57 still holds locks."],
        "remediation": [
            RemediationStep(
                step="Review the blocking query with the app owner.",
                draft_sql=DRAFT_SQL,
            )
        ],
        "read_only_disclaimer": READ_ONLY_DISCLAIMER,
    }
    data.update(overrides)
    return ReportPayload(**data)


def _empty_payload() -> ReportPayload:
    return _payload(
        data_sources=[],
        findings_by_area=[
            AreaFindings(
                area="blocking",
                severity=AreaSeverity.none,
                confidence_percent=0,
                confidence_label=ConfidenceLabel.low,
                signal_count=0,
                findings=[],
                evidence=[],
            )
        ],
        coverage_gaps=[],
        recommendations=[],
        validation_checks=[],
        remediation=[],
        alternatives=[],
    )


def _pdf_text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 1
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# --- render() ---------------------------------------------------------------


@needs_weasyprint
def test_render_produces_a_valid_parseable_pdf():
    pdf = render(_payload())

    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 0

    text = _pdf_text(pdf)
    assert text


@needs_weasyprint
def test_render_contains_expected_text_fragments():
    text = _pdf_text(render(_payload()))

    assert "SQL Server Incident Report" in text
    assert "Acme Corp" in text
    assert "blocking" in text
    assert "recommendation only" in text


@needs_weasyprint
def test_empty_findings_still_render_to_a_pdf():
    pdf = render(_empty_payload())

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 0


@needs_weasyprint
def test_deterministic_document_body_for_identical_payloads():
    first = _pdf_text(render(_payload()))
    second = _pdf_text(render(_payload()))

    assert first == second


# --- render_html() (no WeasyPrint required) ---------------------------------


def test_render_html_contains_every_section8_section():
    html = render_html(_payload())

    for section in (
        'id="identity"',
        'id="sources"',
        'id="findings"',
        'id="causes"',
        'id="gaps"',
        'id="recommendations"',
    ):
        assert section in html
    assert "Area" in html and "Severity" in html and "Confidence" in html
    assert READ_ONLY_DISCLAIMER in unescape(html)


def test_draft_sql_is_preserved_verbatim_inside_a_pre_block():
    html = render_html(_payload())

    assert '<pre class="sql">' in html
    assert DRAFT_SQL in unescape(html)
    assert DRAFT_SQL.count("\n") >= 5


def test_long_evidence_is_marked_for_wrapping():
    html = render_html(_payload())

    assert len(LONG_EVIDENCE) >= 2000
    assert LONG_EVIDENCE in html
    assert 'class="evidence long"' in html


def test_report_filename_is_header_safe_and_has_the_expected_shape():
    name = report_filename(_payload(), 42)

    assert name == "Acme-Corp-42.pdf"
    assert report_filename(_payload(organization="a/b c:d"), 42) == "a-b-c-d-42.pdf"
    assert report_filename(_payload(organization=""), 42) == "report-42.pdf"


def test_render_html_is_deterministic():
    assert render_html(_payload()) == render_html(_payload())


# --- redaction --------------------------------------------------------------


@needs_weasyprint
def test_secret_shaped_values_never_reach_the_pdf():
    secret = "s3cret-P@ssw0rd"
    payload = _payload(
        rationale=f"correlation blocked by password={secret}",
        recommendations=[
            Recommendation(
                area="blocking",
                validation_checks=[f"verify password={secret} is gone"],
                remediation=[
                    RemediationStep(
                        step=f"rotate password={secret}",
                        draft_sql=f"SELECT 'password={secret}';",
                    )
                ],
            )
        ],
    )

    html = render_html(payload)
    text = _pdf_text(render(payload))

    assert secret not in html
    assert secret not in text
    assert "****" in html


# --- endpoint ---------------------------------------------------------------


VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


def _settings():
    from app.config import Settings

    return Settings(
        database_url=VALID_ENV["DATABASE_URL"],
        redis_url=VALID_ENV["REDIS_URL"],
        oidc_issuer=VALID_ENV["OIDC_ISSUER"],
        oidc_client_id=VALID_ENV["OIDC_CLIENT_ID"],
        oidc_client_secret=VALID_ENV["OIDC_CLIENT_SECRET"],
        llm_endpoint=VALID_ENV["LLM_ENDPOINT"],
        llm_api_key=VALID_ENV["LLM_API_KEY"],
    )


def _client(monkeypatch, repository: InMemoryReportRepository) -> TestClient:
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app
    from app.report.dependencies import get_report_repository

    app = create_app(_settings())
    app.dependency_overrides[get_report_repository] = lambda: repository
    return TestClient(app)


def test_endpoint_missing_report_is_404(monkeypatch):
    client = _client(monkeypatch, InMemoryReportRepository())

    response = client.get("/reports/99.pdf")

    assert response.status_code == 404
    assert "no report" in response.json()["detail"]


@needs_weasyprint
def test_endpoint_returns_pdf_with_headers(monkeypatch):
    repository = InMemoryReportRepository()
    repository.put(42, _payload())
    client = _client(monkeypatch, repository)

    response = client.get("/reports/42.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert disposition.endswith("-42.pdf")
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 0


def test_endpoint_failed_render_is_500_without_traceback(monkeypatch):
    repository = InMemoryReportRepository()
    repository.put(42, _payload())
    client = _client(monkeypatch, repository)

    def _boom(payload):  # pragma: no cover - replaces the renderer
        raise PdfRenderError("renderer exploded: secret=leak")

    monkeypatch.setattr("app.report.router.render", _boom)

    response = client.get("/reports/42.pdf")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "the report could not be rendered"
    assert "secret=leak" not in response.text
    assert "Traceback" not in response.text


# --- fixture integrity ------------------------------------------------------


def test_committed_schema_fixture_round_trips_into_the_builder():
    """The #19 schema fixture still validates, so this test's shape cannot drift."""
    schema = json.loads((FIXTURES / "report_schema.json").read_text(encoding="utf-8"))

    assert "findings_by_area" in schema["properties"]
    assert set(_payload().model_dump(mode="json")) == set(schema["properties"])
