"""Append-only strict QA evidence for generated candidates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QaDecision(StrEnum):
    PASS = "PASS"
    REPAIR = "REPAIR"
    REJECT = "REJECT"


class QaVerdict(Base):
    __tablename__ = "qa_verdicts"
    __table_args__ = (
        UniqueConstraint("attempt_id", "candidate_asset_id", name="uq_qa_verdict_attempt_candidate"),
        CheckConstraint("decision IN ('PASS','REPAIR','REJECT')", name="ck_qa_verdict_decision"),
        CheckConstraint("char_length(response_sha256) = 64", name="ck_qa_verdict_response_hash"),
        CheckConstraint("jsonb_typeof(reasons) = 'array'", name="ck_qa_verdict_reasons_array"),
        CheckConstraint("jsonb_typeof(metrics) = 'object'", name="ck_qa_verdict_metrics_object"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_attempts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    checker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[QaDecision] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
