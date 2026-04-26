"""User subscription state model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SubscriptionStatus(str, Enum):
    """Provider-neutral subscription lifecycle states."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class UserSubscription(Base):
    """Current and historical subscription state for one user."""

    __tablename__ = "user_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_user_subscriptions_provider_subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), default="creem", server_default="creem", index=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(String(32), default=SubscriptionStatus.ACTIVE, server_default=SubscriptionStatus.ACTIVE.value, index=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", lazy="selectin")
    plan: Mapped["SubscriptionPlan"] = relationship("SubscriptionPlan", back_populates="subscriptions", lazy="selectin")
    credit_grants: Mapped[list["SubscriptionCreditGrant"]] = relationship(
        "SubscriptionCreditGrant",
        back_populates="subscription",
        lazy="selectin",
    )
