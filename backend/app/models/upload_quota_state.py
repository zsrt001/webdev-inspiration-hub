"""Per-user concurrent upload slot authority."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadQuotaState(Base):
    """Single row locked to reserve or release one of two upload slots."""

    __tablename__ = "upload_quota_states"
    __table_args__ = (
        CheckConstraint(
            "active_slots BETWEEN 0 AND 2",
            name="ck_upload_quota_states_active_slots",
        ),
        CheckConstraint("version >= 0", name="ck_upload_quota_states_version"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    active_slots: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
