from __future__ import annotations

from datetime import date

from sqlalchemy import String, Integer, Date, UniqueConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "usage_date", "user_timezone",
            name="uq_daily_usage_tenant_user_date_tz",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    user_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    total_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
