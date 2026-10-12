"""Optional integration test for the SQL Server connector.

Connects to a real SQL Server through ``DATABASE_URL`` (``mssql+pyodbc`` with
ODBC Driver 18 installed) and runs ``SELECT 1`` through the connector. Skips
visibly unless ``RUN_DB_TESTS=1`` is set AND ``pyodbc`` is importable, so the
default unit suite runs with no database server and no driver.
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import load_settings
from app.sql.connector import connect, query

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the connector integration test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def test_connector_select_one_against_live_server():
    url = make_url(load_settings().database_url)
    if not url.database:
        pytest.skip("DATABASE_URL has no database name to connect to")

    with connect(
        host=url.host or "",
        database=url.database,
        username=url.username or "",
        password=url.password or "",
        port=url.port or 1433,
    ) as conn:
        rows = query(conn, "SELECT 1 AS one")

    assert rows[0]["one"] == 1
    assert rows[0][0] == 1