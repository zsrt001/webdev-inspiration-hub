"""Hash-only, bounded access grant for one private media object."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetAccessGrant(Base):
    """Provider read authority bound to exact runtime and deployment facts."""

    __tablename__ = "asset_access_grants"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_asset_access_grants_token_hash"),
        CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_asset_access_grants_token_hash_length",
        ),
        CheckConstraint(
            "max_reads > 0 AND used_count >= 0 AND used_count <= max_reads",
            name="ck_asset_access_grants_read_count",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_jobs.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_attempts.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    runtime_bundle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_api_deployment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    serving_deployment_role: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    max_reads: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
