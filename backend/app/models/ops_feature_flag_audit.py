"""Append-only audit records for capability state changes."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OpsFeatureFlagAudit(Base):
    __tablename__ = "ops_feature_flag_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_flag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ops_feature_flags.id", ondelete="RESTRICT"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    old_state: Mapped[str] = mapped_column(String(32), nullable=False)
    new_state: Mapped[str] = mapped_column(String(32), nullable=False)
    old_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_bundle_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
