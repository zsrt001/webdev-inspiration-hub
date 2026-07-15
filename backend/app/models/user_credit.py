"""User credit balance model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserCredit(Base):
    """Persistent credit balance per user."""

    __tablename__ = "user_credits"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_credits_user_id"),
        CheckConstraint("reserved_balance >= 0", name="ck_user_credits_reserved_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    balance: Mapped[int] = mapped_column(Integer, default=0)
    reserved_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", lazy="selectin")

    @property
    def accounting_balance(self) -> int:
        return int(self.balance or 0)

    @property
    def spendable_balance(self) -> int:
        return max(0, self.accounting_balance - int(self.reserved_balance or 0))

    @property
    def debt(self) -> int:
        return max(0, -self.accounting_balance)
