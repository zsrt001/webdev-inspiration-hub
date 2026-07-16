"""Live Portrait job SQLAlchemy model (M3 add-on)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class LivePortraitStatus(str, Enum):
    CREATED = "CREATED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LivePortraitJob(Base):
    """Job model for Live Portrait (static image -> short video)."""

    __tablename__ = "live_portrait_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )

    status: Mapped[LivePortraitStatus] = mapped_column(String(32), default=LivePortraitStatus.CREATED, index=True)

    source_image_url: Mapped[str] = mapped_column(String(1024))
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    seconds: Mapped[int] = mapped_column(Integer, default=5)

    video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    generation_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    credits_cost: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        return f"<LivePortraitJob {self.id} status={self.status}>"
