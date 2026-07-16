"""Credit reservations and deterministic grant-lot allocations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class CreditReservation(Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_reservation_user_idempotency"),
        CheckConstraint("amount > 0", name="ck_credit_reservation_amount_positive"),
        CheckConstraint(
            "status IN ('RESERVED', 'CAPTURED', 'RELEASED', 'EXPIRED')",
            name="ck_credit_reservation_status",
        ),
        Index(
            "uq_credit_reservations_active_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'RESERVED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(String(16), nullable=False, default=ReservationStatus.RESERVED)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    funding_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    funding_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_attempts.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    captured_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_retention_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CreditReservationAllocation(Base):
    __tablename__ = "credit_reservation_allocations"
    __table_args__ = (
        UniqueConstraint("reservation_id", "grant_lot_id", name="uq_reservation_allocation_lot"),
        CheckConstraint("amount > 0", name="ck_reservation_allocation_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_reservations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    grant_lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
