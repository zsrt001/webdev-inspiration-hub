"""Append-only migration checkpoints."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataMigrationCheckpoint(Base):
    __tablename__ = "data_migration_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "script_sha256", "mode", "batch_boundary",
            name="uq_data_migration_checkpoint_boundary",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_migration_runs.id", ondelete="RESTRICT"), nullable=False
    )
    script_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_boundary: Mapped[str] = mapped_column(String(256), nullable=False)
    inventory_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval: Mapped[str] = mapped_column(String(160), nullable=False)
    counts_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
