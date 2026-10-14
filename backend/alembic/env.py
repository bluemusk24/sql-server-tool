"""Alembic migration environment.

Wires Alembic to the application's SQLAlchemy metadata so
``alembic revision --autogenerate`` can diff a live database against the
models in ``app/db_models.py``. The database URL is resolved the same way the
app resolves it: an explicit programmatic override (used by the app when it
stamps the baseline), else ``$DATABASE_URL``, else the SQLite default.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the ``app`` package importable when Alembic runs from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_database_url
from app.db_models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: this env.py also runs *inside* the app
    # process (init_schema stamps a freshly bootstrapped database), and the
    # stdlib default would otherwise mute every logger the host application
    # already created (its own sqldiag.* loggers included).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Programmatic override (app stamping) > $DATABASE_URL > SQLite default.
_url = config.attributes.get("sqlalchemy_url") or get_database_url()
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
