"""Durable website-backend execution for generation-job v1."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import uuid
from typing import Any, Awaitable

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.feature_flags import Capability
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import (
    GENERATION_JOB_PAYLOAD_VERSION,
    GenerationJob,
    GenerationJobStatus,
)
from app.models.order import Order
from app.services.evolink_reconciliation_service import reconcile_evolink_attempt
from app.services.delivery_asset_service import (
    build_delivery_assets,
    prepare_delivery_intents_for_terminal_cleanup,
)
from app.services.feature_flag_service import require_backend_capability
from app.services.generation_attempt_service import ensure_accepted_submission_accounting
from app.services.generation_candidate_service import persist_evolink_candidate
from app.services.generation_manual_settlement_service import (
    count_generation_manual_cases,
)
from app.services.generation_repair_service import (
    GENERATION_ATTEMPT_PAYLOAD_VERSION,
    QaDispositionKind,
    decide_next_generation_action,
    fail_generation_and_settle,
)
from app.services.job_lease_service import (
    HEARTBEAT_INTERVAL_SECONDS,
    JobAlreadyLeased,
    JobLease,
    JobNotClaimable,
    JobRequiresReconciliation,
    StaleWorkerFence,
    claim_generation_reconciliation,
    claim_generation_job,
    heartbeat_generation_job,
    pause_generation_reconciliation,
    require_current_generation_fence,
)
from app.services.partner_invite_service import (
    settle_open_partner_consent_case_after_provider,
)
from app.services.qa_verdict_service import QaInfrastructureError, run_and_persist_strict_qa


logger = logging.getLogger(__name__)
settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backend_executor_id() -> str:
    """Return a stable, non-secret lease owner for the website API deployment."""
    deployment_id = settings.deployment_id
    if not deployment_id:
        raise RuntimeError("backend_executor_deployment_missing")
    digest = hashlib.sha256(deployment_id.encode("utf-8")).hexdigest()[:32]
    return f"api:{digest}"


def backend_runtime_coordinates() -> tuple[str, str]:
    """Return the exact deployment coordinates allowed to execute this batch."""
    deployment_id = str(settings.vercel_deployment_id or "").strip()
    runtime_bundle_id = str(settings.runtime_bundle_id or "").strip().lower()
    if not deployment_id or len(deployment_id) > 128:
        raise RuntimeError("backend_executor_deployment_missing")
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime_bundle_id):
        raise RuntimeError("backend_executor_runtime_bundle_invalid")
    return deployment_id, runtime_bundle_id


async def _load_capability_context(job_id: uuid.UUID) -> tuple[GenerationJob, uuid.UUID]:
    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(GenerationJob, Order.user_id)
                .join(Order, Order.id == GenerationJob.order_id)
                .where(GenerationJob.id == job_id)
            )
        ).one_or_none()
        if row is None:
            raise JobNotClaimable("generation_job_not_found", job_id)
        job, user_id = row
        deployment_id, runtime_bundle_id = backend_runtime_coordinates()
        await require_backend_capability(
            db,
            Capability.GENERATION,
            deployment_id=deployment_id,
            runtime_bundle_id=runtime_bundle_id,
            user_id=user_id,
        )
        return job, user_id


async def _load_attempt_capability_context(
    attempt_id: uuid.UUID,
) -> tuple[GenerationJob, GenerationAttempt, uuid.UUID]:
    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(GenerationJob, GenerationAttempt, Order.user_id)
                .join(GenerationAttempt, GenerationAttempt.job_id == GenerationJob.id)
                .join(Order, Order.id == GenerationJob.order_id)
                .where(GenerationAttempt.id == attempt_id)
            )
        ).one_or_none()
        if row is None:
            raise JobNotClaimable("generation_attempt_not_found", attempt_id)
        job, attempt, user_id = row
        if (
            GenerationAttemptKind(attempt.kind) is not GenerationAttemptKind.REPAIR
            or attempt.job_id != job.id
            or job.active_attempt_id != attempt.id
        ):
            raise JobNotClaimable("generation_attempt_not_active_repair", job.id)
        deployment_id, runtime_bundle_id = backend_runtime_coordinates()
        await require_backend_capability(
            db,
            Capability.GENERATION,
            deployment_id=deployment_id,
            runtime_bundle_id=runtime_bundle_id,
            user_id=user_id,
        )
        return job, attempt, user_id


async def _load_reconciliation_context(
    job_id: uuid.UUID,
) -> tuple[GenerationJob, uuid.UUID]:
    """Load accepted work for settlement without reopening submission authority."""

    backend_runtime_coordinates()
    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(GenerationJob, Order.user_id)
                .join(Order, Order.id == GenerationJob.order_id)
                .where(GenerationJob.id == job_id)
            )
        ).one_or_none()
        if row is None:
            raise JobNotClaimable("generation_job_not_found", job_id)
        job, user_id = row
        return job, user_id


async def _load_reconciliation_attempt(
    db,
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
    if (
        attempt is None
        or attempt.job_id != job.id
        or job.active_attempt_id != attempt.id
        or GenerationAttemptStatus(attempt.status)
        not in {
            GenerationAttemptStatus.SUBMITTED,
            GenerationAttemptStatus.UNKNOWN,
            GenerationAttemptStatus.FINISHED,
        }
    ):
        raise JobNotClaimable("generation_reconciliation_attempt_invalid", job.id)
    return attempt


async def _qa_retry_is_exhausted(db, lease: JobLease) -> bool:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    return int(job.retry_count or 0) >= int(settings.generation_max_retries)


async def _pause_reconciliation(
    db,
    *,
    lease: JobLease,
    reason: str,
    retry_after_seconds: int | None,
) -> None:
    await pause_generation_reconciliation(
        db,
        lease=lease,
        reason=reason,
        retry_after_seconds=retry_after_seconds,
    )
    await db.commit()


async def _retry_reconciliation(
    db,
    *,
    lease: JobLease,
    reason: str,
) -> bool:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    if int(job.retry_count or 0) >= int(settings.generation_max_retries):
        return False
    job.retry_count = int(job.retry_count or 0) + 1
    delay = min(60, 5 * (2 ** int(job.retry_count)))
    await _pause_reconciliation(
        db,
        lease=lease,
        reason=reason,
        retry_after_seconds=delay,
    )
    return True


async def _settle_reconciliation_failure(
    db,
    *,
    lease: JobLease,
    reason: str,
    detail: str | None = None,
    allow_release_reserved: bool = False,
) -> None:
    await fail_generation_and_settle(
        db,
        job_id=lease.job_id,
        reason_code=reason,
        reason_detail=detail,
        allow_release_reserved=allow_release_reserved,
        worker_id=lease.worker_id,
        lease_claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=_utcnow(),
    )
    await db.commit()


async def _execute_ready_delivery(
    db,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
) -> None:
    """Publish delivery or atomically stage cleanup before terminal settlement."""

    try:
        await build_delivery_assets(
            db,
            attempt_id=attempt_id,
            lease=lease,
        )
    except StaleWorkerFence:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        reason = str(getattr(exc, "code", "") or type(exc).__name__).lower()[:64]
        if await _retry_reconciliation(
            db,
            lease=lease,
            reason=f"delivery_retry:{reason}"[:64],
        ):
            return
        await prepare_delivery_intents_for_terminal_cleanup(
            db,
            attempt_id=attempt_id,
            lease=lease,
        )
        await _settle_reconciliation_failure(
            db,
            lease=lease,
            reason="delivery_failed",
            detail=reason,
        )


async def _heartbeat_loop(lease: JobLease) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        async with async_session_maker() as db:
            await heartbeat_generation_job(
                db,
                job_id=lease.job_id,
                worker_id=lease.worker_id,
                claim_id=lease.claim_id,
                fencing_token=lease.fencing_token,
            )
            await db.commit()


async def _run_with_heartbeat(lease: JobLease, operation: Awaitable[None]) -> None:
    """Stop Provider work immediately if the database heartbeat loses its fence."""
    work = asyncio.create_task(operation)
    heartbeat = asyncio.create_task(_heartbeat_loop(lease))
    done, pending = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if heartbeat in done:
        await heartbeat
        raise RuntimeError("generation_backend_heartbeat_stopped")
    await work


async def _execute_claimed_generation_job(lease: JobLease, user_id: uuid.UUID) -> None:
    """Delegate to the fenced Provider boundary introduced by Task 18."""
    try:
        from app.services.generation_attempt_service import execute_claimed_generation_job
    except ImportError as exc:
        raise RuntimeError("generation_attempt_runtime_unavailable") from exc
    await execute_claimed_generation_job(lease=lease, user_id=user_id)


async def _execute_claimed_generation_attempt(
    lease: JobLease,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> None:
    """Submit exactly the durable REPAIR row selected from PostgreSQL."""

    try:
        from app.services.generation_attempt_service import execute_claimed_generation_attempt
    except ImportError as exc:
        raise RuntimeError("generation_attempt_runtime_unavailable") from exc
    await execute_claimed_generation_attempt(
        lease=lease,
        user_id=user_id,
        attempt_id=attempt_id,
    )


async def _execute_generation_reconciliation(
    lease: JobLease,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> None:
    """Advance one exact attempt through query, private storage, QA, and disposition."""

    async with async_session_maker() as db:
        attempt = await _load_reconciliation_attempt(
            db,
            attempt_id=attempt_id,
            lease=lease,
        )
        status = GenerationAttemptStatus(attempt.status)
        fact = None
        accounted = False
        if status is GenerationAttemptStatus.SUBMITTED:
            await ensure_accepted_submission_accounting(
                db,
                attempt_id=attempt_id,
                lease=lease,
                user_id=user_id,
            )
            await db.commit()
            accounted = True

        if status is not GenerationAttemptStatus.FINISHED:
            result = await reconcile_evolink_attempt(
                db,
                attempt_id=attempt_id,
                lease=lease,
            )
            if result.state == "UNRESOLVED":
                await _pause_reconciliation(
                    db,
                    lease=lease,
                    reason=result.reason,
                    retry_after_seconds=None,
                )
                return

            if not accounted and result.state != "FAILED":
                await ensure_accepted_submission_accounting(
                    db,
                    attempt_id=attempt_id,
                    lease=lease,
                    user_id=user_id,
                )
                await db.commit()
                accounted = True

            if result.state in {"SUCCEEDED", "FAILED"} and await settle_open_partner_consent_case_after_provider(
                db,
                job_id=lease.job_id,
                attempt_id=attempt_id,
                provider_terminal_state=(
                    result.fact.state.value
                    if result.fact is not None
                    else result.state
                ),
            ):
                await db.commit()
                return

            if result.state == "PENDING":
                current_attempt = await _load_reconciliation_attempt(
                    db,
                    attempt_id=attempt_id,
                    lease=lease,
                )
                submitted_at = current_attempt.submitted_at
                timed_out = (
                    submitted_at is None
                    or _utcnow() - submitted_at
                    >= timedelta(seconds=int(settings.generation_poll_timeout))
                )
                if timed_out:
                    await _settle_reconciliation_failure(
                        db,
                        lease=lease,
                        reason="provider_poll_timeout",
                    )
                    return
                if result.reason == "provider_query_transient":
                    if await _retry_reconciliation(
                        db,
                        lease=lease,
                        reason=result.reason,
                    ):
                        return
                    await _settle_reconciliation_failure(
                        db,
                        lease=lease,
                        reason="provider_query_retry_exhausted",
                    )
                    return
                await _pause_reconciliation(
                    db,
                    lease=lease,
                    reason=result.reason,
                    retry_after_seconds=max(1, int(settings.evolink_poll_interval)),
                )
                return

            if result.state == "FAILED":
                await _settle_reconciliation_failure(
                    db,
                    lease=lease,
                    reason="provider_generation_failed",
                    detail=result.reason,
                    allow_release_reserved=not accounted,
                )
                return
            if result.state != "SUCCEEDED" or result.fact is None:
                raise RuntimeError("generation_reconciliation_result_invalid")
            fact = result.fact
            await persist_evolink_candidate(
                db,
                attempt_id=attempt_id,
                lease=lease,
                fact=fact,
            )

        if await settle_open_partner_consent_case_after_provider(
            db,
            job_id=lease.job_id,
            attempt_id=attempt_id,
            provider_terminal_state="SUCCEEDED",
        ):
            await db.commit()
            return
        exhausted = await _qa_retry_is_exhausted(db, lease)
        try:
            verdict = await run_and_persist_strict_qa(
                db,
                attempt_id=attempt_id,
                lease=lease,
                persist_infrastructure_failure=exhausted,
            )
        except QaInfrastructureError as exc:
            reason = exc.reasons[0] if exc.reasons else "qa_infrastructure_unavailable"
            if not exhausted and await _retry_reconciliation(
                db,
                lease=lease,
                reason=reason,
            ):
                return
            await _settle_reconciliation_failure(
                db,
                lease=lease,
                reason="qa_infrastructure_exhausted",
                detail=",".join(exc.reasons),
            )
            return
        if await settle_open_partner_consent_case_after_provider(
            db,
            job_id=lease.job_id,
            attempt_id=attempt_id,
            provider_terminal_state="SUCCEEDED",
        ):
            await db.commit()
            return
        disposition = await decide_next_generation_action(
            db,
            job_id=lease.job_id,
            verdict_id=verdict.id,
            worker_id=lease.worker_id,
            lease_claim_id=lease.claim_id,
            fencing_token=lease.fencing_token,
        )
        if disposition.kind is QaDispositionKind.READY_FOR_DELIVERY:
            if await settle_open_partner_consent_case_after_provider(
                db,
                job_id=lease.job_id,
                attempt_id=attempt_id,
                provider_terminal_state="SUCCEEDED",
            ):
                await db.commit()
                return
            await _execute_ready_delivery(
                db,
                attempt_id=attempt_id,
                lease=lease,
            )
            return
        await db.commit()


async def generate_order_v1(
    ctx: dict[str, Any],
    job_id: str,
    payload_version: str,
) -> None:
    """Claim one durable PostgreSQL job in the website backend."""
    if payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_job_payload_version_unsupported")
    try:
        parsed_job_id = uuid.UUID(str(job_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("generation_job_id_invalid") from exc
    worker_id = str(ctx.get("worker_id") or "").strip()
    if not worker_id:
        raise RuntimeError("worker_identity_missing")

    job, user_id = await _load_capability_context(parsed_job_id)
    if job.payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_job_database_payload_version_unsupported")
    claim_id = uuid.uuid4()
    async with async_session_maker() as db:
        try:
            lease = await claim_generation_job(
                db,
                job_id=parsed_job_id,
                worker_id=worker_id,
                claim_id=claim_id,
            )
        except JobRequiresReconciliation:
            # The claim function durably routes ambiguous Provider work out of
            # the submission path. Committing here is part of that protocol.
            await db.commit()
            await reconcile_generation_v1(
                {"worker_id": worker_id},
                str(parsed_job_id),
                GENERATION_JOB_PAYLOAD_VERSION,
            )
            return
        except (JobAlreadyLeased, JobNotClaimable):
            return
        else:
            await db.commit()

    # The Task 18 boundary repeats both capability and fence validation directly
    # before every Provider submission; this entry check cannot authorize later I/O.
    await _run_with_heartbeat(lease, _execute_claimed_generation_job(lease, user_id))
    await reconcile_generation_v1(
        {"worker_id": worker_id},
        str(parsed_job_id),
        GENERATION_JOB_PAYLOAD_VERSION,
    )


async def generate_attempt_v1(
    ctx: dict[str, Any],
    attempt_id: str,
    payload_version: str,
) -> None:
    """Claim and submit only the existing REPAIR attempt named by PostgreSQL."""

    if payload_version != GENERATION_ATTEMPT_PAYLOAD_VERSION:
        raise ValueError("generation_attempt_payload_version_unsupported")
    try:
        parsed_attempt_id = uuid.UUID(str(attempt_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("generation_attempt_id_invalid") from exc
    worker_id = str(ctx.get("worker_id") or "").strip()
    if not worker_id:
        raise RuntimeError("worker_identity_missing")

    job, attempt, user_id = await _load_attempt_capability_context(parsed_attempt_id)
    if job.payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_job_database_payload_version_unsupported")
    if attempt.id != parsed_attempt_id or job.active_attempt_id != parsed_attempt_id:
        raise JobNotClaimable("generation_attempt_not_active_repair", job.id)
    claim_id = uuid.uuid4()
    async with async_session_maker() as db:
        try:
            lease = await claim_generation_job(
                db,
                job_id=job.id,
                worker_id=worker_id,
                claim_id=claim_id,
            )
        except JobRequiresReconciliation:
            await db.commit()
            await reconcile_generation_v1(
                {"worker_id": worker_id},
                str(job.id),
                GENERATION_JOB_PAYLOAD_VERSION,
            )
            return
        except (JobAlreadyLeased, JobNotClaimable):
            return
        else:
            await db.commit()
    await _run_with_heartbeat(
        lease,
        _execute_claimed_generation_attempt(lease, user_id, parsed_attempt_id),
    )
    await reconcile_generation_v1(
        {"worker_id": worker_id},
        str(job.id),
        GENERATION_JOB_PAYLOAD_VERSION,
    )


async def reconcile_generation_v1(
    ctx: dict[str, Any],
    job_id: str,
    payload_version: str,
) -> None:
    """Claim one due reconciliation without ever re-entering Provider submit."""

    if payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_reconciliation_payload_version_unsupported")
    try:
        parsed_job_id = uuid.UUID(str(job_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("generation_reconciliation_job_id_invalid") from exc
    worker_id = str(ctx.get("worker_id") or "").strip()
    if not worker_id:
        raise RuntimeError("worker_identity_missing")

    job, user_id = await _load_reconciliation_context(parsed_job_id)
    if job.payload_version != GENERATION_JOB_PAYLOAD_VERSION:
        raise ValueError("generation_job_database_payload_version_unsupported")
    async with async_session_maker() as db:
        try:
            claim = await claim_generation_reconciliation(
                db,
                job_id=parsed_job_id,
                worker_id=worker_id,
                claim_id=uuid.uuid4(),
            )
        except (JobAlreadyLeased, JobNotClaimable):
            return
        await db.commit()
    await _run_with_heartbeat(
        claim.lease,
        _execute_generation_reconciliation(
            claim.lease,
            user_id,
            claim.attempt_id,
        ),
    )


async def execute_generation_job(
    job_id: uuid.UUID,
    *,
    executor_id: str | None = None,
) -> None:
    """Execute one initial job in the website backend without Redis."""
    await generate_order_v1(
        {"worker_id": executor_id or backend_executor_id()},
        str(job_id),
        GENERATION_JOB_PAYLOAD_VERSION,
    )


async def execute_generation_attempt(
    attempt_id: uuid.UUID,
    *,
    executor_id: str | None = None,
) -> None:
    """Execute one already-persisted repair attempt in the website backend."""
    await generate_attempt_v1(
        {"worker_id": executor_id or backend_executor_id()},
        str(attempt_id),
        GENERATION_ATTEMPT_PAYLOAD_VERSION,
    )


async def reconcile_generation_job(
    job_id: uuid.UUID,
    *,
    executor_id: str | None = None,
) -> None:
    """Advance one due Provider task without re-entering submission."""
    await reconcile_generation_v1(
        {"worker_id": executor_id or backend_executor_id()},
        str(job_id),
        GENERATION_JOB_PAYLOAD_VERSION,
    )


async def execute_order_generation_once(
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Best-effort immediate submission after the order transaction commits."""
    await advance_order_generation_once(order_id=order_id, user_id=user_id)


async def advance_order_generation_once(
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    executor_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Advance at most one durable step for one authenticated user's order."""

    current = now or _utcnow()
    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(GenerationJob, Order.user_id)
                .join(Order, Order.id == GenerationJob.order_id)
                .where(Order.id == order_id, Order.user_id == user_id)
            )
        ).one_or_none()
        if row is None:
            raise JobNotClaimable("generation_order_not_found", order_id)
        job, owner_id = row
        if owner_id != user_id:
            raise JobNotClaimable("generation_order_not_found", order_id)

        job_status = GenerationJobStatus(job.status)
        action: tuple[str, uuid.UUID] | None = None
        if (
            job_status is GenerationJobStatus.RECONCILING
            and job.next_retry_at is not None
            and job.next_retry_at <= current
        ):
            action = ("reconcile", job.id)
        elif job_status in {
            GenerationJobStatus.QUEUED,
            GenerationJobStatus.ACTIVE,
        } and (
            job.lease_expires_at is None or job.lease_expires_at <= current
        ):
            attempt = (
                await db.get(GenerationAttempt, job.active_attempt_id)
                if job.active_attempt_id is not None
                else None
            )
            if (
                attempt is not None
                and GenerationAttemptKind(attempt.kind)
                is GenerationAttemptKind.REPAIR
                and GenerationAttemptStatus(attempt.status)
                is GenerationAttemptStatus.PREPARED
            ):
                action = ("attempt", attempt.id)
            else:
                action = ("job", job.id)

    if action is None:
        return "idle"
    kind, entity_id = action
    active_executor = executor_id or backend_executor_id()
    if kind == "reconcile":
        await reconcile_generation_job(entity_id, executor_id=active_executor)
    elif kind == "attempt":
        await execute_generation_attempt(entity_id, executor_id=active_executor)
    else:
        await execute_generation_job(entity_id, executor_id=active_executor)
    return kind


async def _pending_backend_work(
    *,
    limit: int,
    now: datetime,
) -> list[tuple[str, uuid.UUID]]:
    bounded = max(1, min(int(limit), 5))
    # Validate the active backend before reading work. Runtime stamps on jobs
    # remain immutable provenance; generation-job.v1 is the compatibility
    # boundary that permits the active release to finish older work.
    backend_runtime_coordinates()
    async with async_session_maker() as db:
        reconciling = list(
            (
                await db.scalars(
                    select(GenerationJob.id)
                    .where(
                        GenerationJob.status == GenerationJobStatus.RECONCILING,
                        GenerationJob.payload_version
                        == GENERATION_JOB_PAYLOAD_VERSION,
                        GenerationJob.next_retry_at.is_not(None),
                        GenerationJob.next_retry_at <= now,
                    )
                    .order_by(GenerationJob.next_retry_at.asc(), GenerationJob.id.asc())
                    .limit(bounded)
                )
            ).all()
        )
        remaining = bounded - len(reconciling)
        if remaining <= 0:
            return [("reconcile", item) for item in reconciling]
        jobs = list(
            (
                await db.scalars(
                    select(GenerationJob)
                    .where(
                        GenerationJob.status.in_(
                            (GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE)
                        ),
                        GenerationJob.payload_version
                        == GENERATION_JOB_PAYLOAD_VERSION,
                        (
                            GenerationJob.lease_expires_at.is_(None)
                            | (GenerationJob.lease_expires_at <= now)
                        ),
                    )
                    .order_by(GenerationJob.created_at.asc(), GenerationJob.id.asc())
                    .limit(remaining)
                )
            ).all()
        )
        work: list[tuple[str, uuid.UUID]] = [
            ("reconcile", item) for item in reconciling
        ]
        for job in jobs:
            attempt = (
                await db.get(GenerationAttempt, job.active_attempt_id)
                if job.active_attempt_id is not None
                else None
            )
            if (
                attempt is not None
                and GenerationAttemptKind(attempt.kind) is GenerationAttemptKind.REPAIR
                and GenerationAttemptStatus(attempt.status)
                is GenerationAttemptStatus.PREPARED
            ):
                work.append(("attempt", attempt.id))
            else:
                work.append(("job", job.id))
        return work


async def run_backend_generation_maintenance(*, limit: int = 2) -> dict[str, int]:
    """Run a small crash-recovery batch inside one Vercel function invocation."""
    work = await _pending_backend_work(limit=limit, now=_utcnow())
    counts = {
        "selected": len(work),
        "submitted": 0,
        "reconciled": 0,
        "failed": 0,
        "human_required": 0,
    }
    executor_id = backend_executor_id()
    for kind, entity_id in work:
        try:
            if kind == "reconcile":
                await reconcile_generation_job(entity_id, executor_id=executor_id)
                counts["reconciled"] += 1
            elif kind == "attempt":
                await execute_generation_attempt(entity_id, executor_id=executor_id)
                counts["submitted"] += 1
            else:
                await execute_generation_job(entity_id, executor_id=executor_id)
                counts["submitted"] += 1
        except Exception as exc:
            counts["failed"] += 1
            logger.exception(
                "backend_generation_maintenance_item_failed",
                extra={"kind": kind, "error_type": type(exc).__name__},
            )
    async with async_session_maker() as db:
        counts["human_required"] = await count_generation_manual_cases(db)
    return counts
