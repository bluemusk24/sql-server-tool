"""Optional integration test for the Agent-job and error-log collectors (#15).

Runs the shipped ``failed_agent_jobs`` and ``sql_server_error_log`` catalog
queries against a live SQL Server reachable through ``DATABASE_URL``
(``mssql+pyodbc`` with ODBC Driver 18 installed), then feeds the raw #6 rows to
the #15 collectors and asserts the source status is recorded. Skips visibly
unless ``RUN_DB_TESTS=1`` is set AND ``pyodbc`` is importable.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.diagnostics.catalog import load_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.errors import SOURCE_ID as ERROR_SOURCE_ID
from app.evidence.errors import ErrorLogCollector
from app.evidence.jobs import SOURCE_ID as JOB_SOURCE_ID
from app.evidence.jobs import AgentJobsCollector
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.sql import connector

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the agent-jobs collector "
            "integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def test_agent_jobs_collectors_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    catalog = load_catalog()
    registry = EvidenceRegistry(catalog)
    jobs_query = catalog.get(JOB_SOURCE_ID)
    errors_query = catalog.get(ERROR_SOURCE_ID)
    assert jobs_query is not None and errors_query is not None

    with connector.connect(
        host=url.host or "",
        database=url.database,
        username=url.username or "",
        password=url.password or "",
        port=url.port or 1433,
    ) as conn:
        job_rows = connector.query(conn, jobs_query.sql, params=(0, 0))
        error_rows = connector.query(conn, errors_query.sql, params=("",))

    job_findings = AgentJobsCollector().collect(
        SourceEvidence(
            source_id=JOB_SOURCE_ID,
            rows=job_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )
    error_findings = ErrorLogCollector().collect(
        SourceEvidence(
            source_id=ERROR_SOURCE_ID,
            rows=error_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )

    assert registry.get(JOB_SOURCE_ID).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert registry.get(ERROR_SOURCE_ID).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert all(row.area == "agent_jobs" for row in job_findings)
    assert all(row.area == "errors" for row in error_findings)
    assert url.password not in str([row.model_dump() for row in job_findings])
    assert url.password not in str([row.model_dump() for row in error_findings])
