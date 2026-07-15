"""Idempotent per-file upload byte reservation and settlement."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadQuotaReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"


class UploadQuotaReservation(Base):
    """One exact 10 MiB maximum reservation for one multipart file part."""

    __tablename__ = "upload_quota_reservations"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "part_ordinal",
            name="uq_upload_quota_reservations_batch_part",
        ),
        CheckConstraint(
            "status IN ('RESERVED', 'SETTLED', 'RELEASED')",
            name="ck_upload_quota_reservations_status",
        ),
        CheckConstraint(
            "part_ordinal >= 0 AND reserved_bytes >= 0 "
            "AND actual_attempted_bytes >= 0",
            name="ck_upload_quota_reservations_counters",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("upload_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quota_window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("upload_quota_windows.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    part_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_attempted_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    status: Mapped[UploadQuotaReservationStatus] = mapped_column(
        String(16),
        nullable=False,
        default=UploadQuotaReservationStatus.RESERVED,
        server_default=UploadQuotaReservationStatus.RESERVED.value,
        index=True,
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    slot_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
