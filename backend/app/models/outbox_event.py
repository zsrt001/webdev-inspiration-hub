"""Generic IDs-only transactional outbox facts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OutboxEventStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_event_dedupe_key"),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_event_attempts"),
        CheckConstraint("fencing_token >= 0", name="ck_outbox_event_fencing"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_outbox_event_lease_coherent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(192), nullable=False)
    payload_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxEventStatus] = mapped_column(String(16), nullable=False, default=OutboxEventStatus.PENDING, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
