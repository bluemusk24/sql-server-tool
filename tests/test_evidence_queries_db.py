"""Optional integration test for the resource-consuming query collector (#14).

Runs the shipped ``top_elapsed_queries`` catalog query against a live SQL Server
reachable through ``DATABASE_URL`` (``mssql+pyodbc`` with ODBC Driver 18
installed), then feeds the raw #6 rows to the #14 collector and asserts the
source status is recorded. Skips visibly unless ``RUN_DB_TESTS=1`` is set AND
``pyodbc`` is importable.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.diagnostics.catalog import load_catalog
from app.evidence.collectors import SourceEvidence
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.queries import SOURCE_ID, QueriesCollector
from app.evidence.registry import EvidenceRegistry
from app.sql import connector

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the queries collector integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def test_queries_collector_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    catalog = load_catalog()
    registry = EvidenceRegistry(catalog)
    query = catalog.get(SOURCE_ID)
    assert query is not None

    with connector.connect(
        host=url.host or "",
        database=url.database,
        username=url.username or "",
        password=url.password or "",
        port=url.port or 1433,
    ) as conn:
        rows = connector.query(conn, query.sql, params=(1,))

    findings = QueriesCollector().collect(
        SourceEvidence(
            source_id=SOURCE_ID,
            rows=rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )

    assert registry.get(SOURCE_ID).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert all(row.area == "queries" for row in findings)
    assert url.password not in str([row.model_dump() for row in findings])
