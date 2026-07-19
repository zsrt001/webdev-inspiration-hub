"""Durably bind a signed EvoLink terminal callback to one generation attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.evolink_service import (
    EvolinkTaskFact,
    EvolinkTaskState,
    verify_evolink_callback_token,
)


class EvolinkCallbackError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvolinkCallbackResult:
    state: str
    task_id: str


async def bind_evolink_callback_task(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    token: str,
    fact: EvolinkTaskFact,
    now: datetime | None = None,
) -> EvolinkCallbackResult:
    """Persist only the missing Provider correlation; Worker fencing owns all advancement."""

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
    if attempt.provider_job_id is not None:
        if str(attempt.provider_job_id) != fact.task_id:
            raise EvolinkCallbackError("evolink_callback_task_conflict")
        return EvolinkCallbackResult("UNCHANGED", fact.task_id)
    if status in {GenerationAttemptStatus.FINISHED, GenerationAttemptStatus.FAILED}:
        raise EvolinkCallbackError("evolink_callback_terminal_task_missing")

    if job.active_attempt_id != attempt.id:
        raise EvolinkCallbackError("evolink_callback_attempt_not_active")
    attempt.provider_job_id = fact.task_id
    if (
        status is GenerationAttemptStatus.UNKNOWN
        and GenerationJobStatus(job.status) is GenerationJobStatus.RECONCILING
        and job.lease_owner is None
        and job.lease_claim_id is None
        and job.lease_expires_at is None
    ):
        job.next_retry_at = now or datetime.now(timezone.utc)
    return EvolinkCallbackResult("BOUND", fact.task_id)
