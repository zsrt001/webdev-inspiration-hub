"""Subscription plan catalog model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SubscriptionPlan(Base):
    """Commercial subscription plan sold by a hosted payment provider."""

    __tablename__ = "subscription_plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subscription_plans_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    catalog_product_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(16), default="month", server_default="month", index=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), default="USD", server_default="USD")
    monthly_credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    feature_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    subscriptions: Mapped[list["UserSubscription"]] = relationship("UserSubscription", back_populates="plan", lazy="selectin")
