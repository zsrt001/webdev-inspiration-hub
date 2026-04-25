"""Analytics models (click counters)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClickStat(Base):
    """Daily aggregated click counters (CTR tracking)."""

    __tablename__ = "click_stats"
    __table_args__ = (
        UniqueConstraint("day", "event_type", "source_page", "template_id", name="uq_click_stat"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    source_page: Mapped[str] = mapped_column(String(80), index=True)
    template_id: Mapped[str] = mapped_column(String(64), default="na", index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

