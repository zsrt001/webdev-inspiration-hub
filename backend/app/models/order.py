"""Order SQLAlchemy model with status state machine."""

import uuid
from datetime import datetime
from enum import Enum
from sqlalchemy import CheckConstraint, String, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrderStatus(str, Enum):
    """Order status following the defined state machine flow.
    
    Commercial default flow:
    CREATED -> CHECKING -> GENERATING -> COMPLETED

    """

    CREATED = "CREATED"  # Order created, waiting for image upload
    CHECKING = "CHECKING"  # Checking uploaded images (safety/quality)
    GENERATING = "GENERATING"  # AI is generating preview images
    COMPLETED = "COMPLETED"  # Order completed, final images ready
    FAILED = "FAILED"  # Terminal failure after refund/retry protection
    QUEUED = "QUEUED"
    QA_PENDING = "QA_PENDING"
    REPAIRING = "REPAIRING"
    READY = "READY"
    CANCELLED = "CANCELLED"
    UNKNOWN_EXTERNAL_STATE = "UNKNOWN_EXTERNAL_STATE"
    CONSENT_REVIEW_REQUIRED = "CONSENT_REVIEW_REQUIRED"
    DELETED = "DELETED"


class Order(Base):
    """Order model for AI wedding photo generation."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "source_asset_ids IS NULL OR jsonb_typeof(source_asset_ids) = 'array'",
            name="ck_orders_source_asset_ids_array",
        ),
        CheckConstraint(
            "preview_asset_ids IS NULL OR jsonb_typeof(preview_asset_ids) = 'array'",
            name="ck_orders_preview_asset_ids_array",
        ),
        CheckConstraint(
            "final_asset_ids IS NULL OR jsonb_typeof(final_asset_ids) = 'array'",
            name="ck_orders_final_asset_ids_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
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
        comment="Versioned generation request parameters",
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
    source_asset_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Canonical private source asset UUIDs; legacy URL fields are read-only compatibility",
    )
    preview_asset_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Canonical private preview asset UUIDs",
    )
    final_asset_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Canonical private final/delivery asset UUIDs",
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
        comment="Legacy/provider payment transaction identifier",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Task tracking
    task_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Legacy/provider generation task identifier",
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_reservations.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )
    product_policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    funding_policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    settlement_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNSETTLED", server_default="UNSETTLED"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
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
            OrderStatus.CREATED: {OrderStatus.CHECKING, OrderStatus.QUEUED},
            OrderStatus.CHECKING: {OrderStatus.GENERATING, OrderStatus.FAILED, OrderStatus.CANCELLED},
            OrderStatus.QUEUED: {
                OrderStatus.GENERATING,
                OrderStatus.CANCELLED,
                OrderStatus.CONSENT_REVIEW_REQUIRED,
            },
            OrderStatus.GENERATING: {
                OrderStatus.QA_PENDING,
                OrderStatus.FAILED,
                OrderStatus.CANCELLED,
                OrderStatus.UNKNOWN_EXTERNAL_STATE,
                OrderStatus.CONSENT_REVIEW_REQUIRED,
            },
            OrderStatus.QA_PENDING: {
                OrderStatus.REPAIRING,
                OrderStatus.READY,
                OrderStatus.FAILED,
                OrderStatus.CANCELLED,
                OrderStatus.CONSENT_REVIEW_REQUIRED,
            },
            OrderStatus.REPAIRING: {
                OrderStatus.QA_PENDING,
                OrderStatus.FAILED,
                OrderStatus.CANCELLED,
                OrderStatus.UNKNOWN_EXTERNAL_STATE,
                OrderStatus.CONSENT_REVIEW_REQUIRED,
            },
            OrderStatus.UNKNOWN_EXTERNAL_STATE: {
                OrderStatus.GENERATING,
                OrderStatus.FAILED,
                OrderStatus.CANCELLED,
            },
            OrderStatus.CONSENT_REVIEW_REQUIRED: {OrderStatus.CANCELLED, OrderStatus.DELETED},
            OrderStatus.READY: {OrderStatus.DELETED, OrderStatus.CONSENT_REVIEW_REQUIRED},
            OrderStatus.COMPLETED: {OrderStatus.DELETED},
            OrderStatus.FAILED: {OrderStatus.DELETED},
            OrderStatus.CANCELLED: {OrderStatus.DELETED},
            OrderStatus.DELETED: set(),
        }
        current = OrderStatus(self.status)
        return OrderStatus(new_status) in valid_transitions.get(current, set())
