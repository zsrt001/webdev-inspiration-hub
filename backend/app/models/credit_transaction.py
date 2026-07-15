"""Immutable credit ledger model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CreditTransactionType(str, Enum):
    WELCOME_BONUS = "WELCOME_BONUS"
    PURCHASE = "PURCHASE"
    SUBSCRIPTION_GRANT = "SUBSCRIPTION_GRANT"
    GENERATION_DEBIT = "GENERATION_DEBIT"
    GENERATION_REFUND = "GENERATION_REFUND"
    ADMIN_GRANT = "ADMIN_GRANT"
    ADMIN_DEDUCT = "ADMIN_DEDUCT"
    ADJUSTMENT = "ADJUSTMENT"
    ROOT_REVERSAL = "ROOT_REVERSAL"
    PURCHASE_REVERSAL = "PURCHASE_REVERSAL"
    DISPUTE_REVERSAL = "DISPUTE_REVERSAL"
    SUBSCRIPTION_REVERSAL = "SUBSCRIPTION_REVERSAL"
    ACCOUNT_MERGE_OUT = "ACCOUNT_MERGE_OUT"
    ACCOUNT_MERGE_IN = "ACCOUNT_MERGE_IN"


class CreditTransaction(Base):
    """Append-only credit movement record used for audit and reconciliation."""

    __tablename__ = "credit_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    transaction_type: Mapped[CreditTransactionType] = mapped_column(String(32), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    root_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    reversal_of_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped["User"] = relationship("User", lazy="selectin")
