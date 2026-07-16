"""PostgreSQL-authoritative generation job facts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


GENERATION_JOB_PAYLOAD_VERSION = "generation-job.v1"


class GenerationJobStatus(StrEnum):
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    RECONCILING = "RECONCILING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "submission_correlation_id",
            name="uq_generation_jobs_submission_correlation",
        ),
        Index(
            "uq_generation_jobs_nonterminal_order",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED','ACTIVE','RECONCILING')"),
        ),
        Index(
            "uq_generation_jobs_active_lease_claim",
            "lease_claim_id",
            unique=True,
            postgresql_where=text("lease_claim_id IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('QUEUED','ACTIVE','RECONCILING','FINISHED','FAILED','CANCELLED')",
            name="ck_generation_jobs_status",
        ),
        CheckConstraint(
            "retry_count >= 0 AND repair_count >= 0 AND fencing_token >= 0",
            name="ck_generation_jobs_counters",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_generation_jobs_lease_coherent",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR fencing_token > 0",
            name="ck_generation_jobs_active_lease_fenced",
        ),
        CheckConstraint(
            "heartbeat_at IS NULL OR lease_owner IS NOT NULL",
            name="ck_generation_jobs_heartbeat_has_lease",
        ),
        CheckConstraint(
            "payload_version <> 'generation-job.v1' OR "
            "(submission_correlation_id IS NOT NULL AND btrim(api_deployment_id) <> '' "
            "AND btrim(runtime_bundle_id) <> '' AND btrim(expected_worker_image_digest) <> '')",
            name="ck_generation_jobs_v1_runtime_stamps",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    submission_correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    status: Mapped[GenerationJobStatus] = mapped_column(
        String(24), nullable=False, default=GenerationJobStatus.QUEUED, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payload_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GENERATION_JOB_PAYLOAD_VERSION
    )
    api_deployment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_bundle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_worker_image_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    active_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_attempts.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    settlement_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="RESERVED", server_default="RESERVED"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @classmethod
    def queued(
        cls,
        *,
        order_id: uuid.UUID,
        submission_correlation_id: uuid.UUID,
        api_deployment_id: str,
        runtime_bundle_id: str,
        expected_worker_image_digest: str,
    ) -> "GenerationJob":
        if not isinstance(submission_correlation_id, uuid.UUID):
            raise ValueError("submission_correlation_id_invalid")
        stamps = {
            "api_deployment_id": str(api_deployment_id or "").strip(),
            "runtime_bundle_id": str(runtime_bundle_id or "").strip(),
            "expected_worker_image_digest": str(expected_worker_image_digest or "").strip(),
        }
        if any(not value for value in stamps.values()):
            raise ValueError("generation_runtime_stamp_missing")
        return cls(
            id=uuid.uuid4(),
            order_id=order_id,
            submission_correlation_id=submission_correlation_id,
            status=GenerationJobStatus.QUEUED,
            retry_count=0,
            repair_count=0,
            fencing_token=0,
            payload_version=GENERATION_JOB_PAYLOAD_VERSION,
            settlement_status="RESERVED",
            delivery_status="PENDING",
            **stamps,
        )
