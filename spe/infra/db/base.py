"""SQLAlchemy 2.0 ORM declarative base and shared column types."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention makes Alembic auto-generated migrations deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
        list[str]: JSON,
    }


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as timezone-naive UTC and returns aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None

        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # type: ignore[override]
        if value is None:
            return None

        return value.replace(tzinfo=UTC)


class _Mixin:
    """Common columns for tenant-isolated aggregates."""

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
