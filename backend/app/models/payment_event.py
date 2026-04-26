"""Hosted payment webhook event audit model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaymentEvent(Base):
    """Raw provider event record used for webhook idempotency and audit."""

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_payment_events_provider_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), default="creem", server_default="creem", index=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
