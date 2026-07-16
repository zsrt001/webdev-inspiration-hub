"""Normalized external identity bound to one local user."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserIdentity(Base):
    """Authoritative provider subject mapping; legacy user columns are read-only fallback."""

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "subject",
            name="uq_user_identities_provider_subject",
        ),
        CheckConstraint(
            "provider = 'supabase'",
            name="ck_user_identities_provider_supabase",
        ),
        CheckConstraint("btrim(subject) <> ''", name="ck_user_identities_subject_nonempty"),
        Index("ix_user_identities_user_active", "user_id", "revoked_at"),
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
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_email_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
