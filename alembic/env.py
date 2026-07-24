"""Alembic environment.

Runs migrations synchronously. The async production DSN (``postgresql+asyncpg``)
is normalised to its sync driver (``postgresql+psycopg`` or ``psycopg2`` when
available; falls back to the default) purely for the migration connection, so the
same ``SPE_DATABASE_URL`` used by the app also drives Alembic.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from spe.config import get_settings
from spe.infra.db import models  # noqa: F401  (register models on metadata)
from spe.infra.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    url = get_settings().database_url
    # Alembic uses a synchronous engine; strip async drivers.
    return (
        url.replace("+asyncpg", "+psycopg")
        .replace("+aiosqlite", "")
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
