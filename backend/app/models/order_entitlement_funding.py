"""Immutable entitlement funding lineage."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OrderEntitlementFunding(Base):
    __tablename__ = "order_entitlement_fundings"
    __table_args__ = (
        UniqueConstraint("reservation_allocation_id", name="uq_entitlement_funding_allocation"),
        CheckConstraint("amount > 0", name="ck_entitlement_funding_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entitlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_entitlements.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reservation_allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_reservation_allocations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grant_lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
