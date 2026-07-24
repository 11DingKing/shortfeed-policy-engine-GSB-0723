from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, Text,
    Index, func, Column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class SessionDB(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index(
            "ix_sessions_tenant_user_active",
            "tenant_id", "user_id",
            unique=True,
            postgresql_where=Column("status").in_(["active", "paused"]),
        ),
        Index("ix_sessions_tenant_id", "tenant_id"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_policy_version", "tenant_id", "policy_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("policies.id"), nullable=False
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_active_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    user_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    user_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_evaluation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_evaluation_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
