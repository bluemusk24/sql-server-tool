"""Integration tests for the Alembic migration pipeline.

These tests exercise ``alembic upgrade head`` and ``alembic downgrade base``
against a throwaway SQL Server database named ``ssda_test_<uuid>``.

They require a live SQL Server reachable via ``DATABASE_URL`` (``mssql+pyodbc``
with ODBC Driver 18 installed), and the repository login must be able to
``CREATE DATABASE`` so the throwaway database can be created and dropped.

They are skipped unless ``RUN_DB_TESTS=1`` is set, so the default unit suite
runs with no database server. The skip is visible in the pytest summary.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

from app.config import load_settings

ALEMBIC_OWN_TABLE = "alembic_version"
ORGANIZATION_TABLE = "organization"
INVESTIGATION_TABLE = "investigation"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the migration pipeline test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _repository_url() -> URL:
    return make_url(load_settings().database_url)


def _base_table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE'"
            )
        )
        return {row[0] for row in rows}


@pytest.fixture
def throwaway_database(monkeypatch) -> Iterator[tuple[str, Engine, Engine]]:
    """Create ``ssda_test_<uuid>``, yield it, then drop it.

    Yields ``(db_name, target_engine, admin_engine)`` where ``target_engine``
    points at the throwaway database (the one Alembic migrates) and
    ``admin_engine`` points at ``master`` for create/drop.
    """
    url = _repository_url()
    db_name = f"ssda_test_{uuid.uuid4().hex}"
    target_url = url.set(database=db_name)
    target_engine = create_engine(target_url)
    admin_engine = create_engine(
        url.set(database="master"), isolation_level="AUTOCOMMIT"
    )

    with admin_engine.connect() as connection:
        connection.execute(text(f"CREATE DATABASE [{db_name}]"))

    try:
        monkeypatch.setenv(
            "DATABASE_URL", target_url.render_as_string(hide_password=False)
        )
        yield db_name, target_engine, admin_engine
    finally:
        target_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    f"ALTER DATABASE [{db_name}] SET SINGLE_USER "
                    "WITH ROLLBACK IMMEDIATE"
                )
            )
            connection.execute(text(f"DROP DATABASE [{db_name}]"))
        admin_engine.dispose()


def test_upgrade_head_then_downgrade_base(throwaway_database):
    _db_name, target_engine, _admin_engine = throwaway_database
    config = Config(str(ALEMBIC_INI))

    command.upgrade(config, "head")

    after_upgrade = _base_table_names(target_engine)
    assert after_upgrade == {
        ALEMBIC_OWN_TABLE,
        ORGANIZATION_TABLE,
        INVESTIGATION_TABLE,
    }

    command.downgrade(config, "base")

    after_downgrade = _base_table_names(target_engine)
    assert after_downgrade == {ALEMBIC_OWN_TABLE}
