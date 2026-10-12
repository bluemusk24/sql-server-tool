"""Optional integration test for the blocking/deadlock collectors (#13).

Runs the shipped ``blocking_current_chains`` and ``deadlock_system_health``
catalog queries against a live SQL Server reachable through ``DATABASE_URL``
(``mssql+pyodbc`` with ODBC Driver 18 installed), then feeds the raw #6 rows to
the #13 collectors and asserts the source status is recorded. Skips visibly
unless ``RUN_DB_TESTS=1`` is set AND ``pyodbc`` is importable.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.diagnostics.catalog import load_catalog
from app.evidence.blocking import CURRENT_SOURCE_ID, CurrentBlockingCollector
from app.evidence.collectors import SourceEvidence
from app.evidence.deadlocks import SOURCE_ID as DEADLOCK_SOURCE_ID
from app.evidence.deadlocks import DeadlockCollector
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.sql import connector

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the blocking collector integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def test_blocking_collectors_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    catalog = load_catalog()
    registry = EvidenceRegistry(catalog)
    current = catalog.get(CURRENT_SOURCE_ID)
    deadlocks = catalog.get(DEADLOCK_SOURCE_ID)
    assert current is not None and deadlocks is not None

    with connector.connect(
        host=url.host or "",
        database=url.database,
        username=url.username or "",
        password=url.password or "",
        port=url.port or 1433,
    ) as conn:
        blocking_rows = connector.query(conn, current.sql)
        deadlock_rows = connector.query(conn, deadlocks.sql)

    blocking = CurrentBlockingCollector().collect(
        SourceEvidence(
            source_id=CURRENT_SOURCE_ID,
            rows=blocking_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )
    deadlock_findings = DeadlockCollector().collect(
        SourceEvidence(
            source_id=DEADLOCK_SOURCE_ID,
            rows=deadlock_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )

    assert registry.get(CURRENT_SOURCE_ID).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert registry.get(DEADLOCK_SOURCE_ID).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert all(row.area == "blocking" for row in blocking)
    assert all(row.area == "deadlocks" for row in deadlock_findings)
    assert url.password not in str([row.model_dump() for row in blocking])
    assert url.password not in str([row.model_dump() for row in deadlock_findings])
