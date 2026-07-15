"""Durable upload request and byte quota buckets."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadQuotaWindowKind(str, Enum):
    HOURLY_REQUESTS = "HOURLY_REQUESTS"
    DAILY_BYTES = "DAILY_BYTES"


class UploadQuotaWindow(Base):
    """A UTC quota bucket locked before admitting a request or file part."""

    __tablename__ = "upload_quota_windows"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "window_kind",
            "window_start",
            name="uq_upload_quota_windows_user_kind_start",
        ),
        CheckConstraint(
            "window_kind IN ('HOURLY_REQUESTS', 'DAILY_BYTES')",
            name="ck_upload_quota_windows_kind",
        ),
        CheckConstraint(
            "request_count >= 0 AND attempted_bytes >= 0 AND reserved_bytes >= 0",
            name="ck_upload_quota_windows_counters_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    window_kind: Mapped[UploadQuotaWindowKind] = mapped_column(
        String(32), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    attempted_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
