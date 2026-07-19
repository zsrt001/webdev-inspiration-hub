"""Durable, single-flight Creem subscription checkout intent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SubscriptionCheckoutIntentState(str, Enum):
    NEW = "NEW"
    CALLING = "CALLING"
    READY = "READY"
    UNKNOWN = "UNKNOWN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    CONFIRMED = "CONFIRMED"


class SubscriptionCheckoutIntent(Base):
    __tablename__ = "subscription_checkout_intents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_subscription_checkout_user_idempotency",
        ),
        UniqueConstraint(
            "provider_request_id",
            name="uq_subscription_checkout_provider_request",
        ),
        UniqueConstraint(
            "internal_metadata_id",
            name="uq_subscription_checkout_internal_metadata",
        ),
        UniqueConstraint(
            "provider_checkout_id",
            name="uq_subscription_checkout_provider_checkout",
        ),
        UniqueConstraint(
            "provider_subscription_id",
            name="uq_subscription_checkout_provider_subscription",
        ),
        CheckConstraint(
            "state IN ('NEW','CALLING','READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED')",
            name="ck_subscription_checkout_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_subscription_checkout_attempts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    internal_metadata_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    state: Mapped[SubscriptionCheckoutIntentState] = mapped_column(
        String(32),
        default=SubscriptionCheckoutIntentState.NEW,
        server_default=SubscriptionCheckoutIntentState.NEW.value,
        nullable=False,
        index=True,
    )
    catalog_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stored_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_checkout_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    call_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
