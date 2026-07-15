"""Immutable root credit grant lots and spend lineage."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GrantLotSourceType(str, Enum):
    WELCOME = "WELCOME"
    PURCHASE = "PURCHASE"
    SUBSCRIPTION = "SUBSCRIPTION"
    ADMIN = "ADMIN"
    REFUND = "REFUND"
    LEGACY_POOL = "LEGACY_POOL"


class CreditGrantLot(Base):
    __tablename__ = "credit_grant_lots"
    __table_args__ = (
        UniqueConstraint("root_transaction_id", name="uq_credit_grant_lot_root_transaction"),
        CheckConstraint("original_amount > 0", name="ck_credit_grant_lot_original_positive"),
        CheckConstraint(
            "debt_offset_amount >= 0 AND reversed_amount >= 0 AND frozen_amount >= 0 "
            "AND consumed_amount >= 0",
            name="ck_credit_grant_lot_counters_nonnegative",
        ),
        CheckConstraint(
            "debt_offset_amount <= original_amount "
            "AND reversed_amount <= original_amount "
            "AND frozen_amount <= original_amount "
            "AND consumed_amount <= original_amount",
            name="ck_credit_grant_lot_counters_bounded",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    root_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_type: Mapped[GrantLotSourceType] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    original_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    debt_offset_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reversed_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    frozen_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    @property
    def spendable_amount(self) -> int:
        return max(
            0,
            int(self.original_amount)
            - int(self.debt_offset_amount)
            - int(self.reversed_amount)
            - int(self.frozen_amount)
            - int(self.consumed_amount),
        )
