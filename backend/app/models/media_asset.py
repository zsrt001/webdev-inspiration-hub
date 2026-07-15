"""Private media object authority and deletion state machine."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MediaAssetStatus(str, Enum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    ACTIVE = "ACTIVE"
    PENDING_DELETE = "PENDING_DELETE"
    DELETE_FAILED = "DELETE_FAILED"
    DELETED = "DELETED"
    QUARANTINED = "QUARANTINED"


class MediaAssetRole(str, Enum):
    SOURCE = "source"
    INTERMEDIATE = "intermediate"
    CANDIDATE = "candidate"
    QA_INPUT = "qa_input"
    PREVIEW_WATERMARKED = "preview_watermarked"
    FINAL_MASTER = "final_master"
    DELIVERY_VARIANT = "delivery_variant"
    LEGACY_VIDEO = "legacy_video"


class MediaAsset(Base):
    """Authoritative private object record; signed URLs are never persisted."""

    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "storage_provider",
            "object_key",
            name="uq_media_assets_provider_object_key",
        ),
        UniqueConstraint(
            "upload_batch_id",
            "upload_part_ordinal",
            name="uq_media_assets_upload_batch_part",
        ),
        CheckConstraint(
            "role IN ('source', 'intermediate', 'candidate', 'qa_input', "
            "'preview_watermarked', 'final_master', 'delivery_variant', 'legacy_video')",
            name="ck_media_assets_role",
        ),
        CheckConstraint(
            "status IN ('PENDING_UPLOAD', 'UPLOAD_FAILED', 'ACTIVE', "
            "'PENDING_DELETE', 'DELETE_FAILED', 'DELETED', 'QUARANTINED')",
            name="ck_media_assets_status",
        ),
        CheckConstraint(
            "upload_part_ordinal IS NULL OR upload_part_ordinal >= 0",
            name="ck_media_assets_part_ordinal_nonnegative",
        ),
        CheckConstraint(
            "char_length(sha256) = 64",
            name="ck_media_assets_sha256_length",
        ),
        CheckConstraint("byte_size >= 0", name="ck_media_assets_byte_size_nonnegative"),
        CheckConstraint(
            "width IS NULL OR width > 0",
            name="ck_media_assets_width_positive",
        ),
        CheckConstraint(
            "height IS NULL OR height > 0",
            name="ck_media_assets_height_positive",
        ),
        CheckConstraint(
            "delete_attempts >= 0 AND fencing_token >= 0",
            name="ck_media_assets_delete_counters_nonnegative",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_media_assets_delete_lease_coherent",
        ),
        CheckConstraint(
            "status <> 'DELETED' OR deleted_at IS NOT NULL",
            name="ck_media_assets_deleted_timestamp",
        ),
        CheckConstraint(
            "deletion_blockers IS NULL OR jsonb_typeof(deletion_blockers) = 'array'",
            name="ck_media_assets_deletion_blockers_array",
        ),
        CheckConstraint(
            "status NOT IN ('PENDING_DELETE', 'DELETE_FAILED', 'DELETED') "
            "OR btrim(deletion_reason) <> ''",
            name="ck_media_assets_deletion_reason_required",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    upload_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("upload_batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    upload_part_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role: Mapped[MediaAssetRole] = mapped_column(String(32), nullable=False, index=True)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    access_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="private", server_default="private"
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[MediaAssetStatus] = mapped_column(
        String(32),
        nullable=False,
        default=MediaAssetStatus.PENDING_UPLOAD,
        server_default=MediaAssetStatus.PENDING_UPLOAD.value,
        index=True,
    )
    read_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deletion_blockers: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    delete_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_delete_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_delete_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def can_transition_to(self, new_status: MediaAssetStatus) -> bool:
        """Return whether the documented upload/deletion state transition is valid."""

        current = MediaAssetStatus(self.status)
        transitions = {
            MediaAssetStatus.PENDING_UPLOAD: {
                MediaAssetStatus.ACTIVE,
                MediaAssetStatus.UPLOAD_FAILED,
            },
            MediaAssetStatus.UPLOAD_FAILED: {
                MediaAssetStatus.PENDING_DELETE,
                MediaAssetStatus.QUARANTINED,
            },
            MediaAssetStatus.ACTIVE: {
                MediaAssetStatus.PENDING_DELETE,
                MediaAssetStatus.QUARANTINED,
            },
            MediaAssetStatus.QUARANTINED: {MediaAssetStatus.PENDING_DELETE},
            MediaAssetStatus.PENDING_DELETE: {
                MediaAssetStatus.DELETED,
                MediaAssetStatus.DELETE_FAILED,
            },
            MediaAssetStatus.DELETE_FAILED: {MediaAssetStatus.PENDING_DELETE},
            MediaAssetStatus.DELETED: set(),
        }
        return new_status in transitions[current]
