"""Durable release observation run and signed samples."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReleaseObservationRun(Base):
    __tablename__ = "release_observation_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('OBSERVING', 'FINALIZING', 'PASSED', 'FAILED')",
            name="ck_release_observation_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_activation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_bundle_id: Mapped[str] = mapped_column(String(80), nullable=False)
    api_deployment_id: Mapped[str] = mapped_column(String(160), nullable=False)
    worker_deployment_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    worker_image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="OBSERVING", server_default="OBSERVING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleanup_cycle_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    finalizer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReleaseObservationSample(Base):
    __tablename__ = "release_observation_samples"
    __table_args__ = (
        UniqueConstraint(
            "observation_run_id", "bucket_started_at",
            name="uq_release_observation_sample_bucket",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_observation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    bucket_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(512), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ReleaseObservationRecovery(Base):
    __tablename__ = "release_observation_recoveries"
    __table_args__ = (
        UniqueConstraint(
            "observation_run_id",
            name="uq_release_observation_recovery_run",
        ),
        CheckConstraint(
            "disposition IN ('ROLLED_BACK_PRIVATE_BASELINE')",
            name="ck_release_observation_recovery_disposition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    observation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("release_observation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resolution_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    api_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    recovery_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    private_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
