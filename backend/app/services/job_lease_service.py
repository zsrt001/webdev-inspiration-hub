"""PostgreSQL-authoritative Worker leases, heartbeats, and fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.release_activation import ReleaseActivation
from app.services.generation_job_service import validate_job_transition


HEARTBEAT_INTERVAL_SECONDS = 30
JOB_LEASE_SECONDS = 120
MAX_INFRASTRUCTURE_ATTEMPTS = 3
WORKER_HEARTBEAT_MAX_AGE_SECONDS = 120
WORKER_HEARTBEAT_TTL_SECONDS = 150
GENERATION_SCHEMA_REVISION = "20260710_0020"

settings = get_settings()


class JobLeaseError(RuntimeError):
    def __init__(self, code: str, job_id: uuid.UUID):
        self.code = code
        self.job_id = job_id
        super().__init__(code)


class JobNotClaimable(JobLeaseError):
    pass


class JobAlreadyLeased(JobLeaseError):
    pass


class JobRequiresReconciliation(JobLeaseError):
    pass


class StaleWorkerFence(JobLeaseError):
    pass


class WorkerHeartbeatInvalid(RuntimeError):
    pass


class WorkerRuntimeHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    contract_schema: str = Field(default="vowpic.worker-heartbeat.v1", alias="schema")
    worker_id: str
    environment: str
    source_sha: str
    runtime_bundle_id: str
    api_deployment_id: str
    worker_deployment_id: str
    worker_image_digest: str
    schema_revision: str
    payload_min: str
    payload_max: str
    config_hash: str
    current_feature_snapshot_hash: str
    target_feature_snapshot_hash: str
    published_at: datetime


def worker_runtime_heartbeat_key(environment: str, runtime_bundle_id: str) -> str:
    normalized_environment = str(environment or "").strip().lower()
    normalized_bundle = str(runtime_bundle_id or "").strip().lower()
    if normalized_environment not in {"preview", "production"}:
        raise WorkerHeartbeatInvalid("worker_heartbeat_environment_invalid")
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", normalized_bundle):
        raise WorkerHeartbeatInvalid("worker_heartbeat_runtime_bundle_invalid")
    return f"vowpic:worker-heartbeat:v1:{normalized_environment}:{normalized_bundle}"


def worker_runtime_config_hash() -> str:
    """Hash only execution-affecting, non-secret Worker settings."""
    payload = {
        "environment": settings.runtime_environment,
        "release_role": settings.release_role.strip(),
        "generation_engine": settings.generation_engine,
        "evolink_image_model": settings.evolink_image_model,
        "evolink_image_quality": settings.evolink_image_quality,
        "evolink_image_size": settings.evolink_image_size,
        "evolink_poll_interval": settings.evolink_poll_interval,
        "evolink_poll_timeout": settings.evolink_poll_timeout,
        "qa_require_vision": settings.qa_require_vision,
        "qa_require_identity_vision": settings.qa_require_identity_vision,
        "qa_require_identity_embedding": settings.qa_require_identity_embedding,
        "storage_provider": settings.effective_storage_provider,
        "payload_version": "generation-job.v1",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_hex(value: object, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise WorkerHeartbeatInvalid(code)
    return normalized


async def build_worker_runtime_heartbeat(
    db: AsyncSession,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> WorkerRuntimeHeartbeat:
    """Bind a heartbeat to a durable release activation, not process claims."""
    current = now or _utcnow()
    source_sha = settings.source_sha
    runtime_bundle_id = settings.runtime_bundle_id.strip().lower()
    worker_digest = settings.worker_image_digest.strip().lower()
    api_deployment_id = settings.deployment_id
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_sha):
        raise WorkerHeartbeatInvalid("worker_heartbeat_source_sha_invalid")
    worker_runtime_heartbeat_key(settings.runtime_environment, runtime_bundle_id)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", worker_digest):
        raise WorkerHeartbeatInvalid("worker_heartbeat_image_digest_invalid")
    if not api_deployment_id:
        raise WorkerHeartbeatInvalid("worker_heartbeat_api_deployment_missing")
    activation = await db.scalar(
        select(ReleaseActivation)
        .where(
            ReleaseActivation.environment == settings.runtime_environment,
            ReleaseActivation.source_sha == source_sha,
            ReleaseActivation.runtime_bundle_id == runtime_bundle_id,
            ReleaseActivation.api_deployment_id == api_deployment_id,
            ReleaseActivation.worker_image_digest == worker_digest,
            ReleaseActivation.phase.notin_(("FAILED", "CLEANED")),
        )
        .order_by(ReleaseActivation.updated_at.desc(), ReleaseActivation.id.desc())
        .limit(1)
    )
    if activation is None:
        raise WorkerHeartbeatInvalid("worker_release_activation_missing")
    worker_deployment_id = str(activation.worker_deployment_id or "").strip()
    if not worker_deployment_id:
        raise WorkerHeartbeatInvalid("worker_deployment_id_missing")
    return WorkerRuntimeHeartbeat(
        worker_id=_validated_worker_id(worker_id),
        environment=settings.runtime_environment,
        source_sha=source_sha,
        runtime_bundle_id=runtime_bundle_id,
        api_deployment_id=api_deployment_id,
        worker_deployment_id=worker_deployment_id,
        worker_image_digest=worker_digest,
        schema_revision=GENERATION_SCHEMA_REVISION,
        payload_min="generation-job.v1",
        payload_max="generation-job.v1",
        config_hash=worker_runtime_config_hash(),
        current_feature_snapshot_hash=_required_hex(
            activation.current_snapshot_hash,
            "worker_current_feature_snapshot_invalid",
        ),
        target_feature_snapshot_hash=_required_hex(
            activation.target_snapshot_hash,
            "worker_target_feature_snapshot_invalid",
        ),
        published_at=current,
    )


async def publish_worker_runtime_heartbeat(redis, heartbeat: WorkerRuntimeHeartbeat) -> None:
    key = worker_runtime_heartbeat_key(heartbeat.environment, heartbeat.runtime_bundle_id)
    result = await redis.set(
        key,
        heartbeat.model_dump_json(by_alias=True),
        ex=WORKER_HEARTBEAT_TTL_SECONDS,
    )
    if result not in {True, "OK", b"OK"}:
        raise WorkerHeartbeatInvalid("worker_heartbeat_publish_failed")


async def read_worker_runtime_heartbeat(
    redis,
    *,
    environment: str,
    runtime_bundle_id: str,
    now: datetime | None = None,
) -> WorkerRuntimeHeartbeat:
    current = now or _utcnow()
    raw = await redis.get(worker_runtime_heartbeat_key(environment, runtime_bundle_id))
    if raw is None:
        raise WorkerHeartbeatInvalid("worker_heartbeat_missing")
    try:
        heartbeat = WorkerRuntimeHeartbeat.model_validate_json(raw)
    except Exception as exc:
        raise WorkerHeartbeatInvalid("worker_heartbeat_payload_invalid") from exc
    if heartbeat.environment != environment or heartbeat.runtime_bundle_id != runtime_bundle_id:
        raise WorkerHeartbeatInvalid("worker_heartbeat_coordinate_mismatch")
    age = (current - heartbeat.published_at).total_seconds()
    if age < -5 or age > WORKER_HEARTBEAT_MAX_AGE_SECONDS:
        raise WorkerHeartbeatInvalid("worker_heartbeat_stale")
    return heartbeat


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: uuid.UUID
    worker_id: str
    claim_id: uuid.UUID
    fencing_token: int
    heartbeat_at: datetime
    lease_expires_at: datetime

    @classmethod
    def from_job(cls, job: GenerationJob) -> "JobLease":
        if (
            not job.lease_owner
            or job.lease_claim_id is None
            or job.heartbeat_at is None
            or job.lease_expires_at is None
        ):
            raise JobLeaseError("generation_job_lease_incomplete", job.id)
        return cls(
            job_id=job.id,
            worker_id=job.lease_owner,
            claim_id=job.lease_claim_id,
            fencing_token=int(job.fencing_token),
            heartbeat_at=job.heartbeat_at,
            lease_expires_at=job.lease_expires_at,
        )


@dataclass(frozen=True, slots=True)
class ReconciliationClaim:
    lease: JobLease
    attempt_id: uuid.UUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validated_worker_id(worker_id: str) -> str:
    normalized = str(worker_id or "").strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("worker_id_invalid")
    return normalized


async def _lock_job(db: AsyncSession, job_id: uuid.UUID) -> GenerationJob:
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
    )
    if job is None:
        raise JobNotClaimable("generation_job_not_found", job_id)
    return job


async def _attempt_statuses(
    db: AsyncSession,
    job: GenerationJob,
) -> tuple[GenerationAttemptStatus, ...]:
    statement = select(GenerationAttempt.status).where(
        GenerationAttempt.job_id == job.id
    )
    if job.active_attempt_id is not None:
        statement = statement.where(GenerationAttempt.id == job.active_attempt_id)
    values = (
        await db.scalars(
            statement.order_by(GenerationAttempt.attempt_number.asc())
        )
    ).all()
    statuses: list[GenerationAttemptStatus] = []
    for value in values:
        try:
            statuses.append(GenerationAttemptStatus(value))
        except (TypeError, ValueError) as exc:
            raise JobRequiresReconciliation("generation_attempt_status_unknown", job_id) from exc
    return tuple(statuses)


def _clear_lease(job: GenerationJob) -> None:
    job.lease_owner = None
    job.lease_claim_id = None
    job.lease_expires_at = None
    job.heartbeat_at = None


async def claim_generation_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    claim_id: uuid.UUID,
    lease_seconds: int = JOB_LEASE_SECONDS,
    now: datetime | None = None,
) -> JobLease:
    """Claim QUEUED work or safely recover expired PREPARED work."""
    if not isinstance(job_id, uuid.UUID) or not isinstance(claim_id, uuid.UUID):
        raise ValueError("generation_job_claim_identity_invalid")
    owner = _validated_worker_id(worker_id)
    if int(lease_seconds) != JOB_LEASE_SECONDS:
        raise ValueError("generation_job_lease_duration_invalid")
    current = now or _utcnow()
    job = await _lock_job(db, job_id)
    try:
        status = GenerationJobStatus(job.status)
    except (TypeError, ValueError) as exc:
        raise JobNotClaimable("generation_job_status_unknown", job_id) from exc
    if status not in {GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE}:
        raise JobNotClaimable("generation_job_not_claimable", job_id)

    if job.lease_expires_at is not None and job.lease_expires_at > current:
        if job.lease_owner == owner and job.lease_claim_id == claim_id:
            return JobLease.from_job(job)
        raise JobAlreadyLeased("generation_job_already_leased", job_id)

    statuses = await _attempt_statuses(db, job)
    unsafe = {
        GenerationAttemptStatus.SUBMITTING,
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.UNKNOWN,
        GenerationAttemptStatus.FINISHED,
    }
    if any(attempt_status in unsafe for attempt_status in statuses):
        if status != GenerationJobStatus.RECONCILING:
            job.status = validate_job_transition(status, GenerationJobStatus.RECONCILING)
        job.next_retry_at = current
        _clear_lease(job)
        raise JobRequiresReconciliation("generation_job_submission_requires_reconciliation", job_id)

    if status == GenerationJobStatus.QUEUED:
        job.status = validate_job_transition(status, GenerationJobStatus.ACTIVE)
    job.lease_owner = owner
    job.lease_claim_id = claim_id
    job.fencing_token = int(job.fencing_token or 0) + 1
    job.heartbeat_at = current
    job.lease_expires_at = current + timedelta(seconds=JOB_LEASE_SECONDS)
    return JobLease.from_job(job)


async def claim_generation_reconciliation(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    claim_id: uuid.UUID,
    lease_seconds: int = JOB_LEASE_SECONDS,
    now: datetime | None = None,
) -> ReconciliationClaim:
    """Claim an explicitly RECONCILING job without re-entering submission."""

    if not isinstance(job_id, uuid.UUID) or not isinstance(claim_id, uuid.UUID):
        raise ValueError("generation_reconciliation_claim_identity_invalid")
    owner = _validated_worker_id(worker_id)
    if int(lease_seconds) != JOB_LEASE_SECONDS:
        raise ValueError("generation_job_lease_duration_invalid")
    current = now or _utcnow()
    job = await _lock_job(db, job_id)
    if GenerationJobStatus(job.status) is not GenerationJobStatus.RECONCILING:
        raise JobNotClaimable("generation_job_not_reconciling", job_id)
    if job.lease_expires_at is not None and job.lease_expires_at > current:
        raise JobAlreadyLeased("generation_job_already_leased", job_id)

    statement = select(GenerationAttempt).where(
        GenerationAttempt.job_id == job.id,
        GenerationAttempt.status.in_(
            (
                GenerationAttemptStatus.SUBMITTING,
                GenerationAttemptStatus.SUBMITTED,
                GenerationAttemptStatus.UNKNOWN,
                GenerationAttemptStatus.FINISHED,
            )
        ),
    )
    if job.active_attempt_id is not None:
        statement = statement.where(GenerationAttempt.id == job.active_attempt_id)
    attempt = await db.scalar(
        statement.order_by(GenerationAttempt.attempt_number.desc()).with_for_update()
    )
    if attempt is None:
        raise JobNotClaimable("generation_reconciliation_attempt_missing", job_id)

    job.status = validate_job_transition(job.status, GenerationJobStatus.ACTIVE)
    job.lease_owner = owner
    job.lease_claim_id = claim_id
    job.fencing_token = int(job.fencing_token or 0) + 1
    job.heartbeat_at = current
    job.lease_expires_at = current + timedelta(seconds=JOB_LEASE_SECONDS)
    job.next_retry_at = None
    return ReconciliationClaim(
        lease=JobLease.from_job(job),
        attempt_id=attempt.id,
    )


async def pause_generation_reconciliation(
    db: AsyncSession,
    *,
    lease: JobLease,
    reason: str,
    retry_after_seconds: int | None,
    now: datetime | None = None,
) -> GenerationJob:
    """Release a current reconciliation lease and persist its next safe action."""

    current = now or _utcnow()
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=current,
    )
    delay = None if retry_after_seconds is None else int(retry_after_seconds)
    if delay is not None and not 1 <= delay <= 3600:
        raise ValueError("generation_reconciliation_retry_delay_invalid")
    job.status = validate_job_transition(job.status, GenerationJobStatus.RECONCILING)
    job.next_retry_at = None if delay is None else current + timedelta(seconds=delay)
    job.last_error_code = str(reason or "generation_reconciliation_pending")[:64]
    _clear_lease(job)
    return job


def _require_current_fence(
    job: GenerationJob,
    *,
    worker_id: str,
    claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime,
) -> None:
    if (
        job.status != GenerationJobStatus.ACTIVE
        or job.lease_owner != worker_id
        or job.lease_claim_id != claim_id
        or int(job.fencing_token) != int(fencing_token)
        or job.lease_expires_at is None
        or job.lease_expires_at <= now
    ):
        raise StaleWorkerFence("generation_job_stale_worker_fence", job.id)


async def heartbeat_generation_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime | None = None,
) -> JobLease:
    current = now or _utcnow()
    owner = _validated_worker_id(worker_id)
    job = await _lock_job(db, job_id)
    _require_current_fence(
        job,
        worker_id=owner,
        claim_id=claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    job.heartbeat_at = current
    job.lease_expires_at = current + timedelta(seconds=JOB_LEASE_SECONDS)
    return JobLease.from_job(job)


async def require_current_generation_fence(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime | None = None,
) -> GenerationJob:
    """Lock and validate the fence immediately before a durable side effect."""
    current = now or _utcnow()
    owner = _validated_worker_id(worker_id)
    job = await _lock_job(db, job_id)
    _require_current_fence(
        job,
        worker_id=owner,
        claim_id=claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    return job


async def complete_generation_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    claim_id: uuid.UUID,
    fencing_token: int,
    terminal_status: GenerationJobStatus,
    now: datetime | None = None,
) -> GenerationJob:
    current = now or _utcnow()
    target = GenerationJobStatus(terminal_status)
    if target not in {
        GenerationJobStatus.FINISHED,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    }:
        raise ValueError("generation_job_terminal_status_invalid")
    job = await require_current_generation_fence(
        db,
        job_id=job_id,
        worker_id=worker_id,
        claim_id=claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    job.status = validate_job_transition(job.status, target)
    job.finished_at = current
    _clear_lease(job)
    return job
