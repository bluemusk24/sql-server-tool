"""Optional integration test for the diagnostic execution engine (#12).

Runs the engine end to end against a live SQL Server reachable through
``DATABASE_URL`` (``mssql+pyodbc`` with ODBC Driver 18 installed). A single
``SELECT 1`` catalog query is executed through the real #6 connector, and the
credential store is populated from the URL, so the whole #5 -> #10 -> #6 -> #12
composition is exercised. Skips visibly unless ``RUN_DB_TESTS=1`` is set AND
``pyodbc`` is importable.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.credentials import CredentialStore, SqlCredentials
from app.diagnostics.catalog import Catalog, DiagnosticQuery
from app.evidence.models import QueryStatus, SourceStatus
from app.execution import run_diagnostic
from app.investigations.repository import (
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
)

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the execution engine integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _select_one_catalog() -> Catalog:
    catalog = Catalog()
    catalog.register(
        DiagnosticQuery(
            id="integration_select_one",
            title="Select one",
            description="Proves the engine reaches the server read-only.",
            category="availability",
            issue_area="availability",
            sql="SELECT 1 AS one",
            params=(),
            requires_approval=False,
            expected_impact="None.",
            versions=("2008+",),
            min_permission="none",
            read_only=True,
        )
    )
    return catalog


def test_engine_collects_evidence_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    catalog = _select_one_catalog()
    repository = InMemoryInvestigationRepository()
    investigation = repository.create_investigation(
        organization_id=1,
        server_host=url.host or "",
        server_port=url.port or 1433,
        auth_type="sql",
        scope_kind="databases",
        databases=[url.database],
        lookback_hours=1,
        status="draft",
    )
    store = CredentialStore()
    store.store(
        str(investigation.id),
        SqlCredentials(url.username or "", url.password or ""),
    )

    result = run_diagnostic(
        investigation.id,
        investigation_repository=repository,
        approval_repository=InMemoryApprovalRepository(),
        catalog=catalog,
        credential_store=store,
    )

    assert result.status == "succeeded"
    assert result.sources[0].status is SourceStatus.collected
    assert result.evidence[0].status is QueryStatus.ok
    assert result.evidence[0].rows[0]["one"] == 1
    assert url.password not in str(result.model_dump())
