"""User subscription state model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
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


class NormalizedSubscriptionStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class UserSubscription(Base):
    """Current and historical subscription state for one user."""

    __tablename__ = "user_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_user_subscriptions_provider_subscription_id"),
        CheckConstraint(
            "normalized_status IN ('PENDING','ACTIVE','PAST_DUE','CANCEL_REQUESTED','CANCELED','EXPIRED')",
            name="ck_user_subscriptions_normalized_status",
        ),
        Index(
            "uq_user_subscriptions_one_nonterminal",
            "user_id",
            unique=True,
            postgresql_where=text(
                "normalized_status IN ('PENDING','ACTIVE','PAST_DUE','CANCEL_REQUESTED')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
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
    normalized_status: Mapped[NormalizedSubscriptionStatus] = mapped_column(
        String(32),
        default=NormalizedSubscriptionStatus.PENDING,
        server_default=NormalizedSubscriptionStatus.PENDING.value,
        index=True,
    )
    catalog_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    catalog_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_through_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_provider_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_provider_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
