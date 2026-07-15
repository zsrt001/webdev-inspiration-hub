"""Fenced, query-only reconciliation for known Evolink task IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.services.evolink_service import (
    EvolinkProviderError,
    EvolinkService,
    EvolinkTaskFact,
    EvolinkTaskState,
    evolink_service,
)
from app.services.job_lease_service import JobLease, require_current_generation_fence
from app.services.generation_job_service import validate_attempt_transition


class EvolinkReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    state: str
    reason: str
    fact: EvolinkTaskFact | None = None


async def load_reconcilable_attempt(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
) -> GenerationAttempt:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.job_id != job.id:
        raise EvolinkReconciliationError("evolink_attempt_not_found")
    if GenerationAttemptStatus(attempt.status) not in {
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.UNKNOWN,
    }:
        raise EvolinkReconciliationError("evolink_attempt_not_reconcilable")
    return attempt


async def reconcile_evolink_attempt(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    provider: EvolinkService = evolink_service,
) -> ReconciliationResult:
    attempt = await load_reconcilable_attempt(db, attempt_id=attempt_id, lease=lease)
    task_id = str(attempt.provider_job_id or "").strip()
    if not task_id:
        return ReconciliationResult("UNRESOLVED", "provider_task_id_absent")
    try:
        fact = await provider.get_task(task_id)
    except (httpx.TimeoutException, httpx.ConnectError):
        return ReconciliationResult("PENDING", "provider_query_transient")
    except EvolinkProviderError as exc:
        if exc.retryable:
            return ReconciliationResult("PENDING", "provider_query_transient")
        raise

    # Re-lock after all external I/O. A stale claimant cannot apply this fact.
    current = await load_reconcilable_attempt(db, attempt_id=attempt_id, lease=lease)
    if current.provider_job_id != fact.task_id:
        raise EvolinkReconciliationError("evolink_task_identity_mismatch")
    if GenerationAttemptStatus(current.status) is GenerationAttemptStatus.UNKNOWN:
        current.status = validate_attempt_transition(
            current.status,
            GenerationAttemptStatus.SUBMITTED,
        )
        current.submitted_at = current.submitted_at or datetime.now(timezone.utc)
    if fact.state in {EvolinkTaskState.PENDING, EvolinkTaskState.RUNNING}:
        return ReconciliationResult("PENDING", "provider_task_nonterminal", fact)
    if fact.state is EvolinkTaskState.SUCCEEDED:
        return ReconciliationResult("SUCCEEDED", "provider_task_succeeded", fact)
    current.status = validate_attempt_transition(
        current.status,
        GenerationAttemptStatus.FAILED,
    )
    current.finished_at = datetime.now(timezone.utc)
    return ReconciliationResult("FAILED", fact.failure_code or "provider_failed", fact)
