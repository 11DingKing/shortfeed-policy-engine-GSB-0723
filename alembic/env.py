"""Alembic environment.

Runs migrations over an **async** engine because only async drivers
(``asyncpg`` / ``aiosqlite``) are installed. The same ``SPE_DATABASE_URL`` used
by the application drives Alembic, so migrations exercise the identical driver
stack in tests (PostgreSQL via asyncpg) and production.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from spe.config import get_settings
from spe.infra.db import models  # noqa: F401  (register models on metadata)
from spe.infra.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
