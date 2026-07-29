"""Shared SQLAlchemy base types and identifier helpers."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    """Generate a UUID string for externally visible database records."""

    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy table mappings."""


class TimestampMixin:
    """Add database-managed creation and update timestamps to mutable records."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
