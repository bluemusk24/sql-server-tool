"""Integration test for the real approval repository against SQL Server.

Creates a throwaway database (``ssda_test_<uuid>``), migrates it to head, and
round-trips append-only decisions through
:class:`SqlAlchemyApprovalRepository`, asserting that the latest decision per
catalog id is the last appended row and that updating a row is rejected at the
model level.

Skipped unless ``RUN_DB_TESTS=1`` is set, so the default unit suite runs with
no database server. The skip is visible in the pytest summary.
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
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from app.config import load_settings
from app.db.models import AppendOnlyViolation, Approval
from app.db.session import build_session_factory
from app.investigations.repository import SqlAlchemyApprovalRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the approval repository test"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _repository_url() -> URL:
    return make_url(load_settings().database_url)


@pytest.fixture
def throwaway_database(monkeypatch) -> Iterator[Engine]:
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
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield target_engine
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


def test_approval_round_trips_and_is_append_only(throwaway_database):
    engine = throwaway_database
    session_factory = build_session_factory(engine)
    repository = SqlAlchemyApprovalRepository(session_factory)

    first = repository.append_decision(
        investigation_id=1,
        catalog_id="blocking_current_chains",
        decision="approved",
        actor="dba",
        note="reviewed",
    )
    second = repository.append_decision(
        investigation_id=1,
        catalog_id="blocking_current_chains",
        decision="rejected",
        actor="dba",
        note=None,
    )
    repository.append_decision(
        investigation_id=1,
        catalog_id="deadlock_system_health",
        decision="approved",
        actor="dba",
        note=None,
    )

    assert first.id != second.id
    assert len(repository.list_decisions(1)) == 3
    latest = repository.latest_decisions(1)
    assert latest["blocking_current_chains"].decision == "rejected"
    assert latest["deadlock_system_health"].decision == "approved"

    with session_factory() as session:
        stored = session.get(Approval, second.id)
        stored.decision = "approved"
        with pytest.raises(AppendOnlyViolation):
            session.flush()
        session.rollback()

    table_columns = {
        column["name"] for column in inspect(engine).get_columns("approval")
    }
    assert table_columns == {
        "id",
        "investigation_id",
        "catalog_id",
        "decision",
        "actor",
        "note",
        "created_at",
    }
    assert table_columns & {"password", "secret", "username"} == set()
