"""Explicit, non-authoritative email collision between canonical and legacy users."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdentityEmailConflictStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED_MERGED = "RESOLVED_MERGED"
    RESOLVED_DISTINCT = "RESOLVED_DISTINCT"


class IdentityEmailConflict(Base):
    """Idempotent discovery record; matching email never merges accounts by itself."""

    __tablename__ = "identity_email_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "canonical_user_id",
            "legacy_user_id",
            "email_hmac",
            name="uq_identity_email_conflicts_pair_hash",
        ),
        CheckConstraint(
            "canonical_user_id <> legacy_user_id",
            name="ck_identity_email_conflicts_distinct_users",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED_MERGED', 'RESOLVED_DISTINCT')",
            name="ck_identity_email_conflicts_status",
        ),
        CheckConstraint(
            "char_length(email_hmac) = 64",
            name="ck_identity_email_conflicts_hmac_length",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL AND resolution_audit_id IS NULL) "
            "OR (status <> 'OPEN' AND resolved_at IS NOT NULL "
            "AND resolution_audit_id IS NOT NULL)",
            name="ck_identity_email_conflicts_resolution",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    legacy_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    email_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdentityEmailConflictStatus] = mapped_column(
        String(32),
        nullable=False,
        default=IdentityEmailConflictStatus.OPEN,
        server_default=IdentityEmailConflictStatus.OPEN.value,
        index=True,
    )
    discovery_source: Mapped[str] = mapped_column(String(64), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_audit_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
