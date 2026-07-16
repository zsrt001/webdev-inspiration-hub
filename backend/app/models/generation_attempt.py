"""Immutable-bound Provider submission attempts for one generation job."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.generation_job import GenerationJob


class GenerationAttemptKind(StrEnum):
    INITIAL = "INITIAL"
    REPAIR = "REPAIR"
    INFRA_RETRY = "INFRA_RETRY"


class GenerationAttemptStatus(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_generation_attempt_number"),
        UniqueConstraint("provider", "client_request_id", name="uq_generation_attempt_client_request"),
        UniqueConstraint("provider", "provider_job_id", name="uq_generation_attempt_provider_job"),
        UniqueConstraint("source_verdict_id", name="uq_generation_attempt_source_verdict"),
        Index(
            "uq_generation_attempt_initial_job",
            "job_id",
            unique=True,
            postgresql_where=text("kind = 'INITIAL'"),
        ),
        CheckConstraint("attempt_number > 0", name="ck_generation_attempt_number_positive"),
        CheckConstraint(
            "kind IN ('INITIAL','REPAIR','INFRA_RETRY')",
            name="ck_generation_attempt_kind",
        ),
        CheckConstraint(
            "status IN ('PREPARED','SUBMITTING','SUBMITTED','UNKNOWN','FINISHED','FAILED')",
            name="ck_generation_attempt_status",
        ),
        CheckConstraint("cost_minor_units IS NULL OR cost_minor_units >= 0", name="ck_generation_attempt_cost"),
        CheckConstraint(
            "submission_accounting_state IN ('NOT_CAPTURED','PENDING','CAPTURED')",
            name="ck_generation_attempt_accounting_state",
        ),
        CheckConstraint(
            "(kind = 'REPAIR' AND source_verdict_id IS NOT NULL) OR "
            "(kind <> 'REPAIR' AND source_verdict_id IS NULL)",
            name="ck_generation_attempt_repair_verdict",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[GenerationAttemptKind] = mapped_column(String(16), nullable=False)
    status: Mapped[GenerationAttemptStatus] = mapped_column(
        String(16), nullable=False, default=GenerationAttemptStatus.PREPARED, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_verdict_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "qa_verdicts.id",
            name="fk_generation_attempts_source_verdict",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    submission_accounting_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_CAPTURED", server_default="NOT_CAPTURED"
    )
    request_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=True
    )
    cost_minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    submit_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @classmethod
    def prepared(
        cls,
        *,
        job: GenerationJob,
        attempt_number: int,
        kind: GenerationAttemptKind,
        provider: str,
        client_request_id: str | None = None,
        source_verdict_id: uuid.UUID | None = None,
    ) -> "GenerationAttempt":
        normalized_kind = GenerationAttemptKind(kind)
        if normalized_kind is GenerationAttemptKind.INITIAL:
            if source_verdict_id is not None:
                raise ValueError("initial_attempt_source_verdict_forbidden")
            effective_request_id = str(job.submission_correlation_id)
            if client_request_id is not None and client_request_id != effective_request_id:
                raise ValueError("initial_attempt_correlation_mismatch")
        else:
            if normalized_kind is GenerationAttemptKind.REPAIR:
                if not isinstance(source_verdict_id, uuid.UUID):
                    raise ValueError("repair_attempt_source_verdict_missing")
            elif source_verdict_id is not None:
                raise ValueError("infra_retry_source_verdict_forbidden")
            effective_request_id = str(client_request_id or uuid.uuid4())
        if int(attempt_number) <= 0:
            raise ValueError("attempt_number_invalid")
        return cls(
            id=uuid.uuid4(),
            job_id=job.id,
            attempt_number=int(attempt_number),
            kind=normalized_kind,
            status=GenerationAttemptStatus.PREPARED,
            provider=str(provider).strip(),
            client_request_id=effective_request_id,
            source_verdict_id=source_verdict_id,
            submission_accounting_state="NOT_CAPTURED",
        )
