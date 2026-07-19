"""Append-only evidence coordinates for every durable release phase."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReleasePhaseEvidence(Base):
    __tablename__ = "release_phase_evidence"
    __table_args__ = (
        UniqueConstraint(
            "release_activation_id",
            "phase",
            name="uq_release_phase_evidence_phase",
        ),
        UniqueConstraint(
            "release_activation_id",
            "phase_rank",
            name="uq_release_phase_evidence_rank",
        ),
        CheckConstraint("phase_rank > 0", name="ck_release_phase_evidence_rank"),
        CheckConstraint(
            "report_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_release_phase_evidence_report_sha",
        ),
        CheckConstraint(
            "private_object_key !~ '(^|/)\\.\\.?(/|$)'",
            name="ck_release_phase_evidence_object_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    release_activation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("release_activations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    phase_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    private_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    coordinates_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
