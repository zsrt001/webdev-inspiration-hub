"""Hash-only refresh-token generations retained for reuse detection."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RefreshTokenStatus(str, Enum):
    ACTIVE = "ACTIVE"
    USED = "USED"
    REVOKED = "REVOKED"


class AuthRefreshToken(Base):
    """One immutable token generation within an auth-session family."""

    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "generation",
            name="uq_auth_refresh_tokens_session_generation",
        ),
        UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_token_hash"),
        CheckConstraint("generation >= 1", name="ck_auth_refresh_tokens_generation_positive"),
        CheckConstraint(
            "status IN ('ACTIVE', 'USED', 'REVOKED')",
            name="ck_auth_refresh_tokens_status",
        ),
        CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_auth_refresh_tokens_hash_length",
        ),
        CheckConstraint(
            "replacement_token_id IS NULL OR replacement_token_id <> id",
            name="ck_auth_refresh_tokens_replacement_not_self",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND used_at IS NULL AND revoked_at IS NULL "
            "AND replacement_token_id IS NULL) "
            "OR (status = 'USED' AND used_at IS NOT NULL AND revoked_at IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_auth_refresh_tokens_state_timestamps",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RefreshTokenStatus] = mapped_column(
        String(16),
        nullable=False,
        default=RefreshTokenStatus.ACTIVE,
        server_default=RefreshTokenStatus.ACTIVE.value,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replacement_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth_refresh_tokens.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
