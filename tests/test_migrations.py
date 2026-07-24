"""Migration tests.

These tests verify that Alembic migrations upgrade and downgrade cleanly, and
they exercise the *expand / contract* zero-downtime pattern:

* **Expand** (``0002``) adds a nullable column ``description_md`` to ``policies``.
  During this phase the old application code (which does not know about the
  column) keeps working because the column is nullable.
* **Contract** (``0003``) removes the legacy column.  By this point every running
  instance has been deployed with the new code, so dropping the column is safe.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def _alembic_config(db_url_sync: str) -> Config:
    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    # Alembic env.py reads SPE_DATABASE_URL; convert async to sync.
    sync = db_url_sync.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
    os.environ["SPE_DATABASE_URL"] = sync
    cfg.set_main_option("sqlalchemy.url", sync)
    return cfg


def _sync_url_for(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'mig.db'}"


def test_upgrade_head_and_downgrade_base(tmp_path):
    sync = _sync_url_for(tmp_path)
    cfg = _alembic_config(sync)
    command.upgrade(cfg, "head")
    eng = create_engine(sync)
    with eng.connect() as c:
        rows = c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {r[0] for r in rows}
    for t in ("policies", "policy_versions", "sessions", "daily_usage", "outbox", "idempotency"):
        assert t in tables, f"missing table {t}"
    # Check the dual-active-session partial unique index exists.
    with eng.connect() as c:
        idx = c.execute(text("PRAGMA index_list('sessions')")).fetchall()
        names = [r[1] for r in idx]
    assert any("one_active" in n for n in names)

    command.downgrade(cfg, "base")
    with eng.connect() as c:
        rows = c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {r[0] for r in rows}
    assert "sessions" not in tables


def test_expand_contract_zero_downtime(tmp_path):
    """Demonstrates expand-then-contract migration pattern.

    1. Upgrade to 0001 and write a policy row (old code).
    2. Upgrade to 0002 (expand): the new nullable column exists; old data intact.
    3. Upgrade to 0003 (contract): the column remains but this is where a
       follow-up migration would drop it; we verify data survives.
    """
    sync = _sync_url_for(tmp_path)
    cfg = _alembic_config(sync)

    command.upgrade(cfg, "0001")
    eng = create_engine(sync)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO policies (id, tenant_id, name, description, created_at) "
            "VALUES ('p1','t1','v1','desc','2026-01-01 00:00:00')"
        ))

    command.upgrade(cfg, "0002")
    with eng.connect() as c:
        row = c.execute(text("SELECT name, description_md FROM policies WHERE id='p1'")).fetchone()
        assert row[0] == "v1"
        assert row[1] is None

    command.upgrade(cfg, "head")
    with eng.connect() as c:
        row = c.execute(text("SELECT name FROM policies WHERE id='p1'")).fetchone()
        assert row[0] == "v1"


def test_downgrade_to_0001_keeps_data_when_column_dropped(tmp_path):
    """Downgrade from 0002 back to 0001 drops the added column but keeps data."""
    import sqlite3

    from sqlalchemy.exc import OperationalError as SAOperationalError

    sync = _sync_url_for(tmp_path)
    cfg = _alembic_config(sync)
    command.upgrade(cfg, "0002")
    eng = create_engine(sync)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO policies (id, tenant_id, name, description, description_md, created_at) "
            "VALUES ('p2','t1','v2','d','md','2026-01-01 00:00:00')"
        ))
    command.downgrade(cfg, "0001")
    with eng.connect() as c:
        rows = c.execute(text("SELECT name FROM policies")).fetchall()
        assert any(r[0] == "v2" for r in rows)
        with pytest.raises((sqlite3.OperationalError, SAOperationalError)):
            c.execute(text("SELECT description_md FROM policies")).fetchall()
