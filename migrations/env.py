"""Alembic environment for the SQL Server repository.

The database URL is resolved at runtime from the application settings
(``DATABASE_URL`` via :func:`app.config.load_settings`); it is never hard-coded
in ``alembic.ini``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import load_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No domain models yet; later tasks attach their metadata here for autogenerate.
target_metadata = None


def _database_url() -> str:
    """Return the repository URL from application settings at runtime."""
    return load_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode against the settings URL."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the settings URL."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
