"""Revocable local browser session."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuthSession(Base):
    """Database authority for local access-token validation and family revocation."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("family_id", name="uq_auth_sessions_family_id"),
        UniqueConstraint(
            "acceptance_binding_id",
            name="uq_auth_sessions_acceptance_binding_id",
        ),
        CheckConstraint("token_version >= 1", name="ck_auth_sessions_token_version_positive"),
        CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name="ck_auth_sessions_csrf_hash_length",
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
    acceptance_binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("acceptance_identity_bindings.id", ondelete="RESTRICT"),
        nullable=True,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
