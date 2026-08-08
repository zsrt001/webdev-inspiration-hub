"""Durable role-discriminated release activation coordinates."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReleaseActivation(Base):
    __tablename__ = "release_activations"
    __table_args__ = (
        CheckConstraint("environment IN ('preview', 'production')", name="ck_release_activation_environment"),
        CheckConstraint(
            "kind IN ('SAFE_BASELINE_INSTALL', 'PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL', "
            "'COMMERCIAL_7A', 'GOOGLE_AUTH_ONLY', 'CONTRACT_7B')",
            name="ck_release_activation_kind",
        ),
        CheckConstraint(
            "((environment = 'preview' AND kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')) OR "
            "(environment = 'production' AND kind IN "
            "('SAFE_BASELINE_INSTALL', 'COMMERCIAL_7A', 'GOOGLE_AUTH_ONLY', 'CONTRACT_7B')))",
            name="ck_release_activation_environment_kind",
        ),
        CheckConstraint(
            "(api_deployment_id IS NULL AND api_deployment_url IS NULL) OR "
            "(api_deployment_id IS NOT NULL AND api_deployment_url IS NOT NULL)",
            name="ck_release_activation_api_deployment_pair",
        ),
        CheckConstraint(
            "reservation_expires_at IS NULL OR "
            "(reservation_expires_at > created_at AND "
            "reservation_expires_at <= created_at + INTERVAL '2 hours')",
            name="ck_release_activation_reservation_ttl",
        ),
        CheckConstraint(
            "(build_artifact_id IS NULL AND build_artifact_digest IS NULL) OR "
            "(build_artifact_id ~ '^[1-9][0-9]{0,19}$' AND "
            "build_artifact_digest ~ '^sha256:[0-9a-f]{64}$')",
            name="ck_release_activation_build_artifact",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_bundle_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    build_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    build_artifact_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_deployment_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    api_deployment_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_deployment_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    worker_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    private_evidence_prefix: Mapped[str | None] = mapped_column(String(512), nullable=True)
    workflow_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="RESERVED", server_default="RESERVED")
    phase_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    approval: Mapped[str] = mapped_column(String(160), nullable=False)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acceptance_fault_intent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acceptance_fault_intent_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acceptance_fault_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acceptance_fault_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acceptance_fault_cleanup_claim_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acceptance_fault_cleanup_fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
