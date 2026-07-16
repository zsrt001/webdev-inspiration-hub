"""Order delivery entitlement facts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EntitlementStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class OrderEntitlement(Base):
    __tablename__ = "order_entitlements"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_order_entitlement_order"),
        UniqueConstraint(
            "unlock_grant_lot_id",
            name="uq_order_entitlements_unlock_grant_lot",
        ),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_order_entitlement_status"),
        CheckConstraint(
            "(unlock_grant_lot_id IS NULL AND unlock_root_transaction_id IS NULL) OR "
            "(unlock_grant_lot_id IS NOT NULL AND unlock_root_transaction_id IS NOT NULL)",
            name="ck_order_entitlement_unlock_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_reservations.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    unlock_grant_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    unlock_root_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_transactions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[EntitlementStatus] = mapped_column(String(16), nullable=False, default=EntitlementStatus.ACTIVE)
    access_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
