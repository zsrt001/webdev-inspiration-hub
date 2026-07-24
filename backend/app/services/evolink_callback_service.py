"""Durably bind a signed EvoLink terminal callback to one generation attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.evolink_service import (
    EvolinkTaskFact,
    EvolinkTaskState,
    verify_evolink_callback_token,
)
from app.services.generation_job_service import (
    validate_attempt_transition,
    validate_job_transition,
)


class EvolinkCallbackError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvolinkCallbackResult:
    state: str
    task_id: str
    job_id: uuid.UUID


async def bind_evolink_callback_task(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    token: str,
    fact: EvolinkTaskFact,
    now: datetime | None = None,
) -> EvolinkCallbackResult:
    """Bind one terminal Provider fact and make it immediately reconcilable."""

    if not verify_evolink_callback_token(attempt_id, token):
        raise EvolinkCallbackError("evolink_callback_not_found")
    if fact.state in {EvolinkTaskState.PENDING, EvolinkTaskState.RUNNING}:
        raise EvolinkCallbackError("evolink_callback_not_terminal")
    job_id = await db.scalar(
        select(GenerationAttempt.job_id).where(GenerationAttempt.id == attempt_id)
    )
    if job_id is None:
        raise EvolinkCallbackError("evolink_callback_not_found")
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
    )
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if (
        job is None
        or attempt is None
        or attempt.provider != "evolink"
        or attempt.job_id != job.id
    ):
        raise EvolinkCallbackError("evolink_callback_not_found")
    status = GenerationAttemptStatus(attempt.status)
    allowed = {
        GenerationAttemptStatus.SUBMITTING,
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.UNKNOWN,
        GenerationAttemptStatus.FINISHED,
        GenerationAttemptStatus.FAILED,
    }
    if status not in allowed:
        raise EvolinkCallbackError("evolink_callback_attempt_not_submitted")
    if (
        job.active_attempt_id != attempt.id
        and status not in {GenerationAttemptStatus.FINISHED, GenerationAttemptStatus.FAILED}
    ):
        raise EvolinkCallbackError("evolink_callback_attempt_not_active")

    if attempt.provider_job_id is not None:
        if str(attempt.provider_job_id) != fact.task_id:
            raise EvolinkCallbackError("evolink_callback_task_conflict")
        bound = False
    else:
        if status in {GenerationAttemptStatus.FINISHED, GenerationAttemptStatus.FAILED}:
            raise EvolinkCallbackError("evolink_callback_terminal_task_missing")
        attempt.provider_job_id = fact.task_id
        bound = True

    if status in {GenerationAttemptStatus.FINISHED, GenerationAttemptStatus.FAILED}:
        return EvolinkCallbackResult("UNCHANGED", fact.task_id, job.id)

    current = now or datetime.now(timezone.utc)
    if status in {
        GenerationAttemptStatus.SUBMITTING,
        GenerationAttemptStatus.UNKNOWN,
    }:
        attempt.status = validate_attempt_transition(
            status,
            GenerationAttemptStatus.SUBMITTED,
        )
        attempt.submitted_at = attempt.submitted_at or current
        if GenerationAttemptKind(attempt.kind) is GenerationAttemptKind.INITIAL:
            attempt.submission_accounting_state = "PENDING"

    job_status = GenerationJobStatus(job.status)
    lease_active = (
        job.lease_owner is not None
        and job.lease_claim_id is not None
        and job.lease_expires_at is not None
        and job.lease_expires_at > current
    )
    if not lease_active:
        if job_status is GenerationJobStatus.ACTIVE:
            job.status = validate_job_transition(
                job_status,
                GenerationJobStatus.RECONCILING,
            )
        elif job_status is not GenerationJobStatus.RECONCILING:
            raise EvolinkCallbackError("evolink_callback_job_not_reconcilable")
        job.lease_owner = None
        job.lease_claim_id = None
        job.lease_expires_at = None
        job.heartbeat_at = None
    job.next_retry_at = current
    job.last_error_code = None
    job.last_error_detail = None
    return EvolinkCallbackResult(
        "BOUND" if bound else "UNCHANGED",
        fact.task_id,
        job.id,
    )
