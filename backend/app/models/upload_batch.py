"""Durable authenticated upload intent."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadBatchStatus(str, Enum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    ACTIVE = "ACTIVE"
    UPLOAD_FAILED = "UPLOAD_FAILED"


class UploadBatch(Base):
    """One admitted multipart request and its durable concurrent-slot lease."""

    __tablename__ = "upload_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_UPLOAD', 'ACTIVE', 'UPLOAD_FAILED')",
            name="ck_upload_batches_status",
        ),
        CheckConstraint(
            "expected_files BETWEEN 1 AND 5",
            name="ck_upload_batches_expected_files",
        ),
        CheckConstraint(
            "received_files >= 0 AND received_files <= expected_files",
            name="ck_upload_batches_received_files",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[UploadBatchStatus] = mapped_column(
        String(32),
        nullable=False,
        default=UploadBatchStatus.PENDING_UPLOAD,
        server_default=UploadBatchStatus.PENDING_UPLOAD.value,
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    expected_files: Mapped[int] = mapped_column(Integer, nullable=False)
    received_files: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    slot_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
