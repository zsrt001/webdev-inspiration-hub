"""Immutable lineage from one legacy user to a canonical user."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserAccountMerge(Base):
    """Database-guarded one-time merge edge; chains and cycles are forbidden."""

    __tablename__ = "user_account_merges"
    __table_args__ = (
        UniqueConstraint(
            "legacy_user_id",
            name="uq_user_account_merges_legacy_user",
        ),
        UniqueConstraint(
            "claim_proof_id",
            name="uq_user_account_merges_claim_proof",
        ),
        CheckConstraint(
            "canonical_user_id <> legacy_user_id",
            name="ck_user_account_merges_distinct_users",
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
    claim_proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account_claim_proofs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    audit_request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
