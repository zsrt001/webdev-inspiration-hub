"""Durable subscription period-end cancellation intent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CancelIntentState(str, Enum):
    NEW = "NEW"
    CALLING = "CALLING"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"


class SubscriptionCancelIntent(Base):
    __tablename__ = "subscription_cancel_intents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_subscription_cancel_user_idempotency",
        ),
        UniqueConstraint("provider_request_id", name="uq_subscription_cancel_provider_request"),
        CheckConstraint(
            "state IN ('NEW','CALLING','UNKNOWN','CONFIRMED','FAILED_RETRYABLE')",
            name="ck_subscription_cancel_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_subscription_cancel_attempts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[CancelIntentState] = mapped_column(
        String(32), default=CancelIntentState.NEW, server_default=CancelIntentState.NEW.value, nullable=False
    )
    stored_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
