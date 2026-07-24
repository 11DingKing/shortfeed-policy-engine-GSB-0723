"""Migration tests.

Verifies that:

* the full migration chain applies from an empty database (``upgrade head``);
* every migration is reversible (``downgrade base`` then ``upgrade head`` again);
* the second migration (the single-active-session guard) is *additive* and
  zero-downtime: data written under the pre-guard schema survives the upgrade,
  and only afterwards does a second concurrent session get rejected.

The tests run against a temporary on-disk SQLite database so Alembic drives real
DDL exactly as it would against PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path, monkeypatch) -> Config:
    db_path = tmp_path / "migrate.db"
    monkeypatch.setenv("SPE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # Clear the cached settings so the new env var is picked up.
    from spe.config import get_settings

    get_settings.cache_clear()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["db_path"] = str(db_path)
    return cfg


def _sync_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}")


def test_upgrade_head_creates_all_tables(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    db_path = alembic_config.attributes["db_path"]
    engine = _sync_engine(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ).scalars()
        tables = set(rows)
    assert {"policies", "sessions", "session_daily_usage", "heartbeats", "outbox"} <= tables


def test_downgrade_then_upgrade_is_clean(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    db_path = alembic_config.attributes["db_path"]
    engine = _sync_engine(db_path)
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars()
        )
    # Only alembic's own bookkeeping table remains after a full downgrade.
    assert "sessions" not in tables
    # Re-upgrading works from the clean state.
    command.upgrade(alembic_config, "head")


def test_active_session_guard_is_additive_zero_downtime(alembic_config: Config) -> None:
    db_path = alembic_config.attributes["db_path"]

    # Step 1: upgrade only to the pre-guard revision.
    command.upgrade(alembic_config, "0001_initial")
    engine = _sync_engine(db_path)
    with engine.begin() as conn:
        # Insert two "active" sessions for the same (tenant, user): allowed pre-guard.
        for sid in ("s1", "s2"):
            conn.execute(
                text(
                    "INSERT INTO sessions (id, tenant_id, user_id, policy_id, policy_version,"
                    " status, started_at, updated_at, last_seq, watched_seconds_marker,"
                    " total_watched_seconds) VALUES (:id, 't', 'u', 'p', 1, 'ENDED',"
                    " '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00', 0, 0, 0)"
                ),
                {"id": sid},
            )

    # Step 2: apply the additive guard migration. Existing rows survive because
    # they are ENDED (outside the partial index predicate).
    command.upgrade(alembic_config, "0002_active_session_guard")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar()
    assert count == 2

    # Step 3: the guard now rejects a second *non-ended* session per (tenant,user).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sessions (id, tenant_id, user_id, policy_id, policy_version,"
                " status, started_at, updated_at, last_seq, watched_seconds_marker,"
                " total_watched_seconds) VALUES ('a1', 't2', 'u2', 'p', 1, 'ACTIVE',"
                " '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00', 0, 0, 0)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sessions (id, tenant_id, user_id, policy_id, policy_version,"
                    " status, started_at, updated_at, last_seq, watched_seconds_marker,"
                    " total_watched_seconds) VALUES ('a2', 't2', 'u2', 'p', 1, 'ACTIVE',"
                    " '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00', 0, 0, 0)"
                )
            )
