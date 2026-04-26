"""Idempotent subscription period credit grant model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SubscriptionCreditGrant(Base):
    """Tracks monthly subscription credit grants to prevent duplicate issuance."""

    __tablename__ = "subscription_credit_grants"
    __table_args__ = (
        UniqueConstraint("subscription_id", "period_key", name="uq_subscription_credit_grant_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_subscriptions.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    period_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscription: Mapped["UserSubscription"] = relationship("UserSubscription", back_populates="credit_grants", lazy="selectin")
    user: Mapped["User"] = relationship("User", lazy="selectin")
    credit_transaction: Mapped["CreditTransaction"] = relationship("CreditTransaction", lazy="selectin")
