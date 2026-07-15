"""Server-verified proof authorizing one exact legacy-account claim."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountClaimProofType(str, Enum):
    VERIFIED_PAYMENT = "VERIFIED_PAYMENT"
    VERIFIED_SUPPORT_CASE = "VERIFIED_SUPPORT_CASE"


class AccountClaimProof(Base):
    """Hash-only proof fact consumed atomically by one matching merge."""

    __tablename__ = "account_claim_proofs"
    __table_args__ = (
        UniqueConstraint(
            "proof_type",
            "external_reference_hash",
            name="uq_account_claim_proofs_external_reference",
        ),
        UniqueConstraint(
            "consumed_by_merge_id",
            name="uq_account_claim_proofs_consumed_merge",
        ),
        CheckConstraint(
            "proof_type IN ('VERIFIED_PAYMENT', 'VERIFIED_SUPPORT_CASE')",
            name="ck_account_claim_proofs_type",
        ),
        CheckConstraint(
            "char_length(external_reference_hash) = 64",
            name="ck_account_claim_proofs_reference_hash_length",
        ),
        CheckConstraint(
            "(consumed_at IS NULL AND consumed_by_merge_id IS NULL) "
            "OR (consumed_at IS NOT NULL AND consumed_by_merge_id IS NOT NULL)",
            name="ck_account_claim_proofs_consumption_pair",
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
    proof_type: Mapped[AccountClaimProofType] = mapped_column(String(32), nullable=False)
    external_reference_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_actor: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_merge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "user_account_merges.id",
            name="fk_account_claim_proofs_consumed_merge",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    audit_request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
