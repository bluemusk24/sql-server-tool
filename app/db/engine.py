"""SQLAlchemy engine creation for the repository SQL Server.

The engine URL comes from the #2 settings object (``DATABASE_URL``). Engine
creation is lazy: importing this module neither imports the ODBC DBAPI nor
opens a connection, so unit tests run without a driver or a live server.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from app.config import Settings, load_settings

_engine: Engine | None = None


def build_engine(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine for the repository database from settings."""
    return create_engine(settings.database_url)


def get_engine() -> Engine:
    """Return the process-wide engine, created lazily from application settings."""
    global _engine
    if _engine is None:
        _engine = build_engine(load_settings())
    return _engine
