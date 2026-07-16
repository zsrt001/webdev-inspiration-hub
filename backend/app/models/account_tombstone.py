"""Minimal durable account-closure marker."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountTombstone(Base):
    """Prevents hard deletion while closure and private-media cleanup remain auditable."""

    __tablename__ = "account_tombstones"
    __table_args__ = (
        CheckConstraint(
            "btrim(closure_reason) <> ''",
            name="ck_account_tombstones_reason_nonempty",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    closure_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    media_cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    audit_request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
