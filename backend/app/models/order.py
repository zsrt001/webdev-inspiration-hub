"""Order SQLAlchemy model with status state machine."""

import uuid
from datetime import datetime
from enum import Enum
from sqlalchemy import String, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrderStatus(str, Enum):
    """Order status following the defined state machine flow.
    
    Commercial default flow:
    CREATED -> CHECKING -> GENERATING -> COMPLETED

    Legacy statuses are kept for historical record compatibility only.
    """

    CREATED = "CREATED"  # Order created, waiting for image upload
    CHECKING = "CHECKING"  # Checking uploaded images (safety/quality)
    GENERATING = "GENERATING"  # AI is generating preview images
    PREVIEW_READY = "PREVIEW_READY"  # Compatibility-only legacy status
    PAID = "PAID"  # Compatibility-only legacy status
    UPSCALING = "UPSCALING"  # Compatibility-only legacy status
    COMPLETED = "COMPLETED"  # Order completed, final images ready
    FAILED = "FAILED"  # Terminal failure after refund/retry protection


class Order(Base):
    """Order model for AI wedding photo generation."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        String(32),
        default=OrderStatus.CREATED,
        index=True,
    )

    # AI Generation Config
    template_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Selected template ID",
    )
    style_template: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Selected wedding photo style template",
    )
    generation_params: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="InstantID generation parameters",
    )

    # Image URLs (stored in S3)
    source_image_urls: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Original uploaded image URLs (deleted after processing)",
    )
    preview_image_urls: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Generated preview image URLs",
    )
    final_image_urls: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Final high-resolution image URLs",
    )
    source_images_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When uploaded source images should be removed from storage",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When generated assets should be removed from storage",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When order assets were removed or user requested deletion",
    )
    storage_cleanup_status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        server_default="active",
        index=True,
        comment="active | source_deleted | deleted | cleanup_failed",
    )

    # Payment
    price_cents: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Price in cents (分)",
    )
    payment_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="WeChat payment transaction ID",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Task tracking
    task_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="ARQ/Celery task ID for tracking",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if generation failed",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="orders",
    )

    def __repr__(self) -> str:
        return f"<Order {self.id} status={self.status}>"

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        """Check if status transition is valid according to state machine."""
        valid_transitions = {
            OrderStatus.CREATED: [OrderStatus.CHECKING],
            OrderStatus.CHECKING: [OrderStatus.GENERATING, OrderStatus.CREATED, OrderStatus.FAILED],
            OrderStatus.GENERATING: [OrderStatus.COMPLETED, OrderStatus.CREATED, OrderStatus.FAILED],
            OrderStatus.PREVIEW_READY: [OrderStatus.COMPLETED],  # Compatibility-only legacy path
            OrderStatus.PAID: [OrderStatus.UPSCALING, OrderStatus.COMPLETED],  # Compatibility-only legacy path
            OrderStatus.UPSCALING: [OrderStatus.COMPLETED, OrderStatus.PAID],  # Compatibility-only legacy path
            OrderStatus.COMPLETED: [],
            OrderStatus.FAILED: [OrderStatus.CREATED, OrderStatus.GENERATING],
        }
        return new_status in valid_transitions.get(self.status, [])
