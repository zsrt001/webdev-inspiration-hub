"""Durable migration parent/child leases."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataMigrationRun(Base):
    __tablename__ = "data_migration_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_migration_runs.id", ondelete="RESTRICT"), nullable=True
    )
    release_activation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    runtime_bundle_id: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    inventory_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    script_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    approval: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    counts_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
