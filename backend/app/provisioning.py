"""Per-organization database provisioning.

Registering an organization in the shared repository control database also
creates a physically isolated SQL Server database for it, named
``ssda_org_<id>``. The identifier is derived only from the organization's
numeric ``id`` and never from the user-supplied ``name`` or ``sso_issuer``,
which avoids identifier quoting and injection concerns.

The repository login must have ``CREATE DATABASE`` permission (the
``dbcreator`` server role or an equivalent grant): provisioning creates a
database, runs the repository migrations inside it, and drops that database
again if migrating it fails so no orphan databases are left behind.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.db.models import Organization

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def organization_database_name(organization_id: int) -> str:
    """Return the isolated database name derived from an organization id."""
    if organization_id <= 0:
        raise ValueError("organization_id must be a positive integer")
    return f"ssda_org_{organization_id}"


def _get_engine(session: Session) -> Engine:
    """Return the repository engine the session is bound to."""
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        raise TypeError("session must be bound to a SQLAlchemy Engine")
    return bind


def _admin_engine(base_engine: Engine) -> Engine:
    """Create an AUTOCOMMIT engine on ``master`` for CREATE/DROP DATABASE."""
    return create_engine(
        base_engine.url.set(database="master"),
        isolation_level="AUTOCOMMIT",
    )


def _create_database(base_engine: Engine, database_name: str) -> None:
    """Create ``database_name`` on the server behind ``base_engine``."""
    admin = _admin_engine(base_engine)
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE [{database_name}]"))
    finally:
        admin.dispose()


def _drop_database(base_engine: Engine, database_name: str) -> None:
    """Force-drop ``database_name`` on the server behind ``base_engine``."""
    admin = _admin_engine(base_engine)
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    f"ALTER DATABASE [{database_name}] SET SINGLE_USER "
                    "WITH ROLLBACK IMMEDIATE"
                )
            )
            connection.execute(text(f"DROP DATABASE [{database_name}]"))
    finally:
        admin.dispose()


def _run_migrations(base_engine: Engine, database_name: str) -> None:
    """Apply the repository migrations to the newly created database."""
    target_url = base_engine.url.set(database=database_name).render_as_string(
        hide_password=False
    )
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
    command.upgrade(config, "head")


def provision_organization(
    session: Session, name: str, sso_issuer: str
) -> Organization:
    """Register an organization and provision its isolated database.

    Inserts the organization row, derives ``ssda_org_<id>`` from the generated
    id, creates the database, and runs the migrations inside it. If migrating
    the new database fails, the database is dropped before the error
    propagates. The caller owns the transaction and commits the inserted row.
    """
    organization = Organization(name=name, sso_issuer=sso_issuer)
    session.add(organization)
    session.flush()

    database_name = organization_database_name(organization.id)
    base_engine = _get_engine(session)
    _create_database(base_engine, database_name)
    try:
        _run_migrations(base_engine, database_name)
    except Exception:
        _drop_database(base_engine, database_name)
        raise
    return organization
