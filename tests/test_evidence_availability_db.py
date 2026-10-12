"""Optional integration test for the availability and storage collectors (#16).

Runs the shipped ``non_online_databases`` and ``database_volume_space`` catalog
queries against a live SQL Server reachable through ``DATABASE_URL``
(``mssql+pyodbc`` with ODBC Driver 18 installed), then feeds the raw #6 rows to
the #16 collectors and asserts the source status is recorded. Skips visibly
unless ``RUN_DB_TESTS=1`` is set AND ``pyodbc`` is importable.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.diagnostics.catalog import load_catalog
from app.evidence.availability import SOURCE_ID as AVAILABILITY_SOURCE
from app.evidence.availability import AvailabilityCollector
from app.evidence.collectors import SourceEvidence
from app.evidence.models import QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.evidence.storage import SOURCE_ID as STORAGE_SOURCE
from app.evidence.storage import DatabaseVolumeCollector
from app.sql import connector

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the availability/storage "
            "collector integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def test_availability_and_storage_collectors_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    catalog = load_catalog()
    registry = EvidenceRegistry(catalog)
    availability_query = catalog.get(AVAILABILITY_SOURCE)
    storage_query = catalog.get(STORAGE_SOURCE)
    assert availability_query is not None and storage_query is not None

    with connector.connect(
        host=url.host or "",
        database=url.database,
        username=url.username or "",
        password=url.password or "",
        port=url.port or 1433,
    ) as conn:
        availability_rows = connector.query(conn, availability_query.sql, params=())
        storage_rows = connector.query(conn, storage_query.sql, params=())

    availability_findings = AvailabilityCollector().collect(
        SourceEvidence(
            source_id=AVAILABILITY_SOURCE,
            rows=availability_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )
    storage_findings = DatabaseVolumeCollector().collect(
        SourceEvidence(
            source_id=STORAGE_SOURCE,
            rows=storage_rows,
            status=QueryStatus.ok,
            source_status=SourceStatus.collected,
        ),
        registry,
    )

    assert registry.get(AVAILABILITY_SOURCE).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert registry.get(STORAGE_SOURCE).status in {
        SourceStatus.collected,
        SourceStatus.not_applicable,
    }
    assert all(row.area == "availability" for row in availability_findings)
    assert all(row.area == "storage" for row in storage_findings)
    assert url.password not in str([row.model_dump() for row in availability_findings])
    assert url.password not in str([row.model_dump() for row in storage_findings])
