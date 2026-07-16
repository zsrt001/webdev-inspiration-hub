"""One verified identity, one welcome grant claim."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WelcomeGrantClaim(Base):
    __tablename__ = "welcome_grant_claims"
    __table_args__ = (
        UniqueConstraint("user_identity_id", name="uq_welcome_grant_claim_identity"),
        UniqueConstraint("user_id", name="uq_welcome_grant_claim_user"),
        UniqueConstraint("credit_transaction_id", name="uq_welcome_grant_claim_transaction"),
        UniqueConstraint("grant_lot_id", name="uq_welcome_grant_claim_lot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_identities.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credit_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    grant_lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_grant_lots.id", ondelete="RESTRICT"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
