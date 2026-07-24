"""Alembic environment.

Uses the shared SQLAlchemy ``Base.metadata`` so that migration auto-generation
sees the full schema.  The database URL is taken from the ``SPE_DATABASE_URL``
environment variable when present, falling back to the value in ``alembic.ini``.
Both sync (for ``alembic upgrade``) and async drivers are supported: when an
async driver is detected we run migrations through ``run_sync``.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from spe.infra.db.base import Base
from spe.infra.db import models  # noqa: F401  (register tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

URL = os.environ.get("SPE_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
# Alembic does not support async drivers directly; replace with sync equivalent.
SYNC_URL = (
    URL.replace("+aiosqlite", "")
       .replace("+asyncpg", "+psycopg2")
)


def run_migrations_offline() -> None:
    context.configure(
        url=SYNC_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(SYNC_URL, poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
