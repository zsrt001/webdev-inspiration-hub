"""Durable ARQ schedules and the generation-job v1 entrypoint."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
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
from app.services.feature_flag_service import require_worker_capability
from app.services.generation_attempt_service import ensure_accepted_submission_accounting
from app.services.generation_candidate_service import persist_evolink_candidate
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
    build_worker_runtime_heartbeat,
    claim_generation_reconciliation,
    claim_generation_job,
    heartbeat_generation_job,
    pause_generation_reconciliation,
    publish_worker_runtime_heartbeat,
    require_current_generation_fence,
)
from app.services.outbox_service import publish_pending_generation_outbox
from app.services.partner_invite_service import (
    settle_open_partner_consent_case_after_provider,
)
from app.services.qa_verdict_service import QaInfrastructureError, run_and_persist_strict_qa


logger = logging.getLogger(__name__)
settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    configured = str(os.getenv("WORKER_INSTANCE_ID") or "").strip()
    value = configured or f"{socket.gethostname()}:{os.getpid()}"
    if len(value) > 128:
        raise RuntimeError("worker_instance_id_too_long")
    return value


async def startup_worker(ctx: dict[str, Any]) -> None:
    """Establish process identity and fail closed unless release binding is valid."""
    ctx["worker_id"] = _worker_id()
    await publish_worker_heartbeat(ctx)


async def publish_worker_heartbeat(ctx: dict[str, Any]) -> None:
    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("worker_redis_context_missing")
    worker_id = str(ctx.get("worker_id") or "").strip()
    if not worker_id:
        raise RuntimeError("worker_identity_missing")
    async with async_session_maker() as db:
        heartbeat = await build_worker_runtime_heartbeat(db, worker_id=worker_id)
    await publish_worker_runtime_heartbeat(redis, heartbeat)


async def dispatch_generation_outbox(ctx: dict[str, Any]) -> None:
    """Publish committed PostgreSQL facts using ARQ's deterministic job IDs."""
    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("worker_redis_context_missing")
    async with async_session_maker() as db:
        await publish_pending_generation_outbox(db, redis, limit=50)
        await db.commit()


async def dispatch_generation_reconciliation(
    ctx: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Enqueue only due PostgreSQL facts; the fenced claim is authoritative."""

    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("worker_redis_context_missing")
    current = now or _utcnow()
    async with async_session_maker() as db:
        jobs = list(
            (
                await db.scalars(
                    select(GenerationJob)
                    .where(
                        GenerationJob.status == GenerationJobStatus.RECONCILING,
                        GenerationJob.next_retry_at.is_not(None),
                        GenerationJob.next_retry_at <= current,
                    )
                    .order_by(GenerationJob.next_retry_at.asc(), GenerationJob.id.asc())
                    .limit(25)
                )
            ).all()
        )
    bucket = int(current.timestamp()) // 5
    for job in jobs:
        await redis.enqueue_job(
            "reconcile_generation_v1",
            str(job.id),
            GENERATION_JOB_PAYLOAD_VERSION,
            _job_id=f"generation-reconcile:v1:{job.id}:{bucket}",
        )


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
        await require_worker_capability(
            db,
            Capability.GENERATION,
            deployment_id=job.api_deployment_id,
            runtime_bundle_id=job.runtime_bundle_id,
            worker_image_digest=job.expected_worker_image_digest,
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
        await require_worker_capability(
            db,
            Capability.GENERATION,
            deployment_id=job.api_deployment_id,
            runtime_bundle_id=job.runtime_bundle_id,
            worker_image_digest=job.expected_worker_image_digest,
            user_id=user_id,
        )
        return job, attempt, user_id


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
        raise RuntimeError("generation_worker_heartbeat_stopped")
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
    """Submit exactly the durable REPAIR row carried by the outbox message."""

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
    """Claim one durable job; Redis contains no user, image, price, or secret."""
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
            return
        except (JobAlreadyLeased, JobNotClaimable):
            return
        else:
            await db.commit()

    # The Task 18 boundary repeats both capability and fence validation directly
    # before every Provider submission; this entry check cannot authorize later I/O.
    await _run_with_heartbeat(lease, _execute_claimed_generation_job(lease, user_id))


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
            return
        except (JobAlreadyLeased, JobNotClaimable):
            return
        else:
            await db.commit()
    await _run_with_heartbeat(
        lease,
        _execute_claimed_generation_attempt(lease, user_id, parsed_attempt_id),
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

    job, user_id = await _load_capability_context(parsed_job_id)
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
