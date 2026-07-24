"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime


class PolicyRow(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PolicyVersionRow(Base):
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "policy_id", "version", name="uq_policy_version"),
        Index(
            "ix_policy_versions_current",
            "tenant_id",
            "policy_id",
            unique=True,
            postgresql_where=(is_current.is_(True)),
            sqlite_where=(is_current.is_(True)),
        ),
    )


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_document: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_resumed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    accumulated_active_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_age: Mapped[int] = mapped_column(Integer, nullable=False)
    user_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    approvals: Mapped[list[str]] = mapped_column(default=list)

    __table_args__ = (
        # Partial unique index: at most one active/paused session per user/tenant.
        Index(
            "ix_sessions_one_active_per_user",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=(state.in_(["ACTIVE", "PAUSED"])),
            sqlite_where=(state.in_(["ACTIVE", "PAUSED"])),
        ),
        Index("ix_sessions_tenant_user", "tenant_id", "user_id"),
    )


class DailyUsageRow(Base):
    __tablename__ = "daily_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "local_date", name="uq_daily_usage_user_date"),
    )


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    response: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_key"),
    )
