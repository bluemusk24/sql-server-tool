"""Database wiring: engine, session factory, and the request-scoped session.

The database is chosen by the ``SDIP_DATABASE_URL`` environment variable
(then ``DATABASE_URL``, then a SQLite default). Because everything goes
through SQLAlchemy, pointing the app at Postgres later is just a matter
of changing that one variable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite:///./sqlserver_tool.db"


def normalize_database_url(url: str) -> str:
    """Pin Postgres URLs to the psycopg 3 driver.

    A bare ``postgresql://`` (or the legacy ``postgres://`` alias) makes
    SQLAlchemy load the psycopg2 dialect, which is not installed; every
    Postgres URL is rewritten to ``postgresql+psycopg://`` so
    ``postgresql://user:pass@host:5432/db`` just works. SQLite URLs pass
    through untouched.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def get_database_url() -> str:
    """Resolve the configured database URL.

    ``SDIP_DATABASE_URL`` is the product-prefixed name used by the
    container run command (``-e SDIP_DATABASE_URL=sqlite:////data/sdip.db``);
    plain ``DATABASE_URL`` remains supported for local shells and other
    platforms. SQLite is the default when neither is set. Postgres URLs
    are normalized to the installed driver (see
    :func:`normalize_database_url`).
    """
    return normalize_database_url(
        os.getenv("SDIP_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def make_engine(database_url: str) -> Engine:
    """Build an engine for the URL, adding SQLite-specific connect options."""
    kwargs: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a threadpool; SQLite's default
        # same-thread check would reject cross-thread use.
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite:///:memory:", "sqlite://"):
            # One shared connection so every session sees the same
            # in-memory database (used to isolate tests).
            kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False keeps ORM attributes readable after commit.
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed on success."""
    session: Session = request.app.state.SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_schema(engine: Engine, database_url: str) -> None:
    """Create tables if missing and keep Alembic's baseline in sync.

    ``create_all`` bootstraps a fresh database with zero configuration. If
    Alembic is present we then ``stamp head`` so that a later
    ``alembic upgrade`` doesn't try to re-create the same tables.

    Once ``alembic_version`` exists the stamp is skipped entirely — it only
    ever matters for a freshly bootstrapped database, and avoiding it keeps
    Alembic (a heavy import) out of the normal startup path.
    """
    from app.db_models import Base

    Base.metadata.create_all(engine, checkfirst=True)
    if ":memory:" in database_url:
        return  # ephemeral test databases don't need migration tracking
    if inspect(engine).has_table("alembic_version"):
        return  # schema already tracked; nothing to stamp
    _stamp_alembic(database_url)


def _stamp_alembic(database_url: str) -> None:
    """Mark the schema as at ``head`` in ``alembic_version`` (best effort)."""
    try:
        from alembic.config import Config

        from alembic import command

        ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        if not ini.exists():
            return
        cfg = Config(str(ini))
        # Let env.py connect to exactly this database, not just $DATABASE_URL.
        cfg.attributes["sqlalchemy_url"] = database_url
        command.stamp(cfg, "head")
    except Exception:  # noqa: BLE001
        # Alembic is optional at runtime; never block startup over it.
        return
