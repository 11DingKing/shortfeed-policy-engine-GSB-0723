"""Application configuration via pydantic-settings.

All settings are environment-overridable (prefix ``SPE_``). The default database
URL uses an in-process async SQLite file so the service and its tests can run
without a live PostgreSQL, while production points ``SPE_DATABASE_URL`` at a
PostgreSQL DSN (``postgresql+asyncpg://...``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(env_prefix="SPE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./spe.db"
    """SQLAlchemy async DSN. Use ``postgresql+asyncpg://...`` in production."""

    echo_sql: bool = False
    """When true, SQLAlchemy logs emitted SQL (development aid)."""

    heartbeat_max_gap_seconds: int = 90
    """Upper bound on watch-time credited by a single heartbeat interval.

    Guards against a client that goes silent then sends one huge heartbeat; the
    gap between two heartbeats is clamped to this many seconds.
    """

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return process-wide cached settings."""
    return Settings()
