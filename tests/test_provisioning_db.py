"""Integration tests for per-organization provisioning against a live server.

These tests create a throwaway control database (``ssda_test_<uuid>``), migrate
it to head, and provision organizations so their isolated ``ssda_org_<id>``
databases are created on a real SQL Server reachable via ``DATABASE_URL``
(``mssql+pyodbc`` with ODBC Driver 18 installed). The repository login must be
able to ``CREATE DATABASE`` and ``DROP DATABASE``.

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import provisioning
from app.config import load_settings
from app.db.session import build_session_factory
from app.provisioning import provision_organization

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"


def _skip_reason() -> str | None:
    if not RUN_DB_TESTS:
        return (
            "DB tests disabled: set RUN_DB_TESTS=1 with a live SQL Server "
            "reachable via DATABASE_URL to run the provisioning tests"
        )
    if importlib.util.find_spec("pyodbc") is None:
        return "pyodbc/ODBC Driver 18 not installed; cannot reach SQL Server"
    return None


_SKIP_REASON = _skip_reason()
pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _repository_url() -> URL:
    return make_url(load_settings().database_url)


def _database_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT name FROM sys.databases"))
        return {row[0] for row in rows}


def _organization_databases(engine: Engine) -> set[str]:
    return {name for name in _database_names(engine) if name.startswith("ssda_org_")}


def _force_drop(engine: Engine, database_name: str) -> None:
    with engine.connect() as connection:
        connection.execute(
            text(
                f"ALTER DATABASE [{database_name}] SET SINGLE_USER "
                "WITH ROLLBACK IMMEDIATE"
            )
        )
        connection.execute(text(f"DROP DATABASE [{database_name}]"))


@pytest.fixture
def control_database(monkeypatch) -> Iterator[tuple[Engine, Engine, Session]]:
    """Yield a migrated throwaway control database, then drop it and its orgs.

    Yields ``(control_engine, admin_engine, session)``.
    """
    url = _repository_url()
    db_name = f"ssda_test_{uuid.uuid4().hex}"
    target_url = url.set(database=db_name)
    control_engine = create_engine(target_url)
    admin_engine = create_engine(
        url.set(database="master"), isolation_level="AUTOCOMMIT"
    )

    with admin_engine.connect() as connection:
        connection.execute(text(f"CREATE DATABASE [{db_name}]"))

    monkeypatch.setenv(
        "DATABASE_URL", target_url.render_as_string(hide_password=False)
    )
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    session = build_session_factory(control_engine)()
    try:
        yield control_engine, admin_engine, session
    finally:
        session.close()
        control_engine.dispose()
        for name in sorted(_organization_databases(admin_engine)):
            _force_drop(admin_engine, name)
        _force_drop(admin_engine, db_name)
        admin_engine.dispose()


def test_two_organizations_get_distinct_databases(control_database):
    _control_engine, admin_engine, session = control_database

    first = provision_organization(session, "Org A", "https://issuer.example/a")
    session.commit()
    second = provision_organization(session, "Org B", "https://issuer.example/b")
    session.commit()

    assert first.id != second.id
    names = _database_names(admin_engine)
    assert f"ssda_org_{first.id}" in names
    assert f"ssda_org_{second.id}" in names


def test_probe_rows_do_not_mix_between_organizations(control_database):
    control_engine, _admin_engine, session = control_database

    first = provision_organization(session, "Org A", "https://issuer.example/a")
    session.commit()
    second = provision_organization(session, "Org B", "https://issuer.example/b")
    session.commit()

    first_engine = create_engine(
        control_engine.url.set(database=f"ssda_org_{first.id}")
    )
    second_engine = create_engine(
        control_engine.url.set(database=f"ssda_org_{second.id}")
    )
    try:
        with first_engine.begin() as connection:
            connection.execute(text("CREATE TABLE probe (id int)"))
            connection.execute(text("INSERT INTO probe (id) VALUES (1)"))
        with second_engine.begin() as connection:
            connection.execute(text("CREATE TABLE probe (id int)"))
            connection.execute(text("INSERT INTO probe (id) VALUES (2)"))

        with first_engine.connect() as connection:
            assert connection.execute(text("SELECT id FROM probe")).scalar() == 1
        with second_engine.connect() as connection:
            assert connection.execute(text("SELECT id FROM probe")).scalar() == 2
    finally:
        first_engine.dispose()
        second_engine.dispose()


def test_duplicate_name_leaves_no_database_behind(control_database):
    _control_engine, admin_engine, session = control_database

    provision_organization(session, "Org A", "https://issuer.example/a")
    session.commit()
    before = _organization_databases(admin_engine)

    with pytest.raises(IntegrityError):
        provision_organization(session, "Org A", "https://issuer.example/other")
    session.rollback()

    assert _organization_databases(admin_engine) == before


def test_duplicate_issuer_leaves_no_database_behind(control_database):
    _control_engine, admin_engine, session = control_database

    provision_organization(session, "Org A", "https://issuer.example/a")
    session.commit()
    before = _organization_databases(admin_engine)

    with pytest.raises(IntegrityError):
        provision_organization(session, "Org B", "https://issuer.example/a")
    session.rollback()

    assert _organization_databases(admin_engine) == before


def test_migration_failure_drops_the_new_database(control_database, monkeypatch):
    _control_engine, admin_engine, session = control_database
    before = _organization_databases(admin_engine)

    def boom(_engine, _database_name):
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(provisioning, "_run_migrations", boom)

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        provision_organization(session, "Org A", "https://issuer.example/a")
    session.rollback()

    assert _organization_databases(admin_engine) == before
