"""Integration test for the real investigation repository against SQL Server.

Creates a throwaway database (``ssda_test_<uuid>``), migrates it to head,
seeds an organization, and round-trips an investigation through
:class:`SqlAlchemyInvestigationRepository`. It also inspects the real table to
confirm it has no credential columns.

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
from app.db.models import Organization
from app.db.session import build_session_factory
from app.investigations.repository import SqlAlchemyInvestigationRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"

CREDENTIAL_FIELD_NAMES = {
    "password",
    "pwd",
    "secret",
    "api_key",
    "token",
    "username",
    "user",
    "connection_string",
    "conn_str",
}


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the investigation repository test"
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


def test_investigation_round_trips_and_has_no_credential_columns(throwaway_database):
    engine = throwaway_database
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        organization = Organization(
            name="Acme", sso_issuer="https://issuer.example.com/acme"
        )
        session.add(organization)
        session.commit()
        organization_id = organization.id

    repository = SqlAlchemyInvestigationRepository(session_factory)

    resolved = repository.get_organization_id_by_issuer(
        "https://issuer.example.com/acme"
    )
    assert resolved == organization_id

    record = repository.create_investigation(
        organization_id=organization_id,
        server_host="sql01.example.com",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=4,
        status="draft",
    )
    assert record.id > 0
    assert record.status == "draft"

    with session_factory() as session:
        stored = session.execute(
            text(
                "SELECT organization_id, server_host, server_port, auth_type, "
                "scope_kind, databases, lookback_hours, status FROM investigation "
                "WHERE id = :id"
            ),
            {"id": record.id},
        ).one()
    assert stored.organization_id == organization_id
    assert stored.server_host == "sql01.example.com"
    assert stored.scope_kind == "instance"
    assert stored.lookback_hours == 4
    assert stored.status == "draft"

    columns = {column["name"] for column in inspect(engine).get_columns("investigation")}
    assert columns & CREDENTIAL_FIELD_NAMES == set()
