"""Application configuration loaded from environment variables."""
from __future__ import annotations

from dataclasses import dataclass
from os import environ


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./spe.db"
    echo_sql: bool = False
    api_prefix: str = "/api/v1"
    outbox_poll_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=environ.get("SPE_DATABASE_URL", "sqlite+aiosqlite:///./spe.db"),
            echo_sql=environ.get("SPE_ECHO_SQL", "0") == "1",
            api_prefix=environ.get("SPE_API_PREFIX", "/api/v1"),
            outbox_poll_interval_seconds=float(
                environ.get("SPE_OUTBOX_POLL_INTERVAL", "1.0")
            ),
        )


def current_settings() -> Settings:
    return Settings.from_env()
