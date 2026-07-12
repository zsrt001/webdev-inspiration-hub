"""Authoritative high-risk capability state."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OpsFeatureFlag(Base):
    __tablename__ = "ops_feature_flags"
    __table_args__ = (
        UniqueConstraint("environment", "capability", name="uq_ops_feature_flag_environment_capability"),
        CheckConstraint("environment IN ('preview', 'production')", name="ck_ops_feature_flag_environment"),
        CheckConstraint("state IN ('OFF', 'ACCEPTANCE_COHORT', 'ON')", name="ck_ops_feature_flag_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="OFF", server_default="OFF")
    deployment_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_bundle_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    worker_image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    release_activation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=True
    )
    target_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cohort_user_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    verified_identity_hashes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
