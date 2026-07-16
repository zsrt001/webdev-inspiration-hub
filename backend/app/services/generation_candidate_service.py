"""Fenced transfer of one Evolink result into private candidate authority."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Awaitable, Callable
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.services.evolink_service import EvolinkTaskFact, EvolinkTaskState
from app.services.external_fetch_service import fetch_admin_https
from app.services.generation_job_service import validate_attempt_transition
from app.services.job_lease_service import JobLease, require_current_generation_fence
from app.services.media_asset_service import ValidatedImageBytes
from app.services.partner_invite_service import require_partner_generation_allowed
from app.services.storage import PrivateObjectStore, storage_service


settings = get_settings()
logger = logging.getLogger(__name__)
_CANDIDATE_NAMESPACE = uuid.UUID("9e4d42e3-03f8-4f29-967f-70d1ba5fa3f1")


class GenerationCandidateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateContext:
    job: GenerationJob
    attempt: GenerationAttempt
    order: Order
    existing_asset: MediaAsset | None


async def _load_candidate_context(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    now: datetime,
) -> CandidateContext:
    await require_partner_generation_allowed(db, job_id=lease.job_id)
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=now,
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
        not in {GenerationAttemptStatus.SUBMITTED, GenerationAttemptStatus.FINISHED}
    ):
        raise GenerationCandidateError("generation_candidate_attempt_invalid")
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if order is None or order.generation_job_id != job.id:
        raise GenerationCandidateError("generation_candidate_order_invalid")

    kind = GenerationAttemptKind(attempt.kind)
    if kind is GenerationAttemptKind.INITIAL:
        if attempt.submission_accounting_state != "CAPTURED":
            raise GenerationCandidateError("generation_candidate_capture_pending")
    elif kind is GenerationAttemptKind.REPAIR:
        if attempt.submission_accounting_state != "NOT_CAPTURED":
            raise GenerationCandidateError("generation_repair_capture_state_invalid")
        from app.services.generation_repair_service import _load_repair_capture_lineage

        await _load_repair_capture_lineage(db, job)
    else:
        raise GenerationCandidateError("generation_candidate_attempt_kind_invalid")

    existing = None
    if attempt.result_asset_id is not None:
        existing = await db.scalar(
            select(MediaAsset)
            .where(MediaAsset.id == attempt.result_asset_id)
            .with_for_update()
        )
        if (
            existing is None
            or existing.job_id != job.id
            or existing.order_id != order.id
            or existing.owner_user_id != order.user_id
            or MediaAssetRole(existing.role) is not MediaAssetRole.CANDIDATE
            or MediaAssetStatus(existing.status) is not MediaAssetStatus.ACTIVE
            or existing.read_revoked_at is not None
        ):
            raise GenerationCandidateError("generation_candidate_existing_invalid")
    return CandidateContext(job=job, attempt=attempt, order=order, existing_asset=existing)


def build_candidate_intent(
    *,
    context: CandidateContext,
    validated: ValidatedImageBytes,
    now: datetime,
) -> MediaAsset:
    if now.tzinfo is None:
        raise ValueError("generation_candidate_time_must_be_aware")
    asset_id = uuid.uuid5(_CANDIDATE_NAMESPACE, str(context.attempt.id))
    expires_at = getattr(context.order, "expires_at", None)
    if expires_at is None or expires_at <= now:
        expires_at = now + timedelta(days=30)
    return MediaAsset(
        id=asset_id,
        owner_user_id=context.order.user_id,
        order_id=context.order.id,
        job_id=context.job.id,
        parent_asset_id=None,
        role=MediaAssetRole.CANDIDATE,
        storage_provider=settings.effective_storage_provider,
        object_key=(
            f"users/{context.order.user_id}/generation/{context.job.id}/"
            f"attempts/{context.attempt.id}/candidate-{asset_id}.jpg"
        ),
        sha256=validated.sha256,
        mime_type=validated.mime_type,
        byte_size=validated.byte_size,
        width=validated.width,
        height=validated.height,
        access_level="private",
        policy_version="generation-candidate.v1",
        expires_at=expires_at,
        status=MediaAssetStatus.PENDING_UPLOAD,
    )


async def _load_or_create_candidate_intent(
    db: AsyncSession,
    *,
    context: CandidateContext,
    validated: ValidatedImageBytes,
    now: datetime,
) -> MediaAsset:
    expected = build_candidate_intent(context=context, validated=validated, now=now)
    asset = await db.scalar(
        select(MediaAsset).where(MediaAsset.id == expected.id).with_for_update()
    )
    if asset is None:
        db.add(expected)
        await db.flush()
        await db.commit()
        return expected
    immutable_fields = (
        asset.owner_user_id == expected.owner_user_id,
        asset.order_id == expected.order_id,
        asset.job_id == expected.job_id,
        MediaAssetRole(asset.role) is MediaAssetRole.CANDIDATE,
        asset.object_key == expected.object_key,
        asset.sha256 == expected.sha256,
        int(asset.byte_size) == int(expected.byte_size),
        asset.width == expected.width,
        asset.height == expected.height,
    )
    if not all(immutable_fields) or MediaAssetStatus(asset.status) not in {
        MediaAssetStatus.PENDING_UPLOAD,
        MediaAssetStatus.ACTIVE,
    }:
        raise GenerationCandidateError("generation_candidate_intent_conflict")
    return asset


async def _reject_source_duplicate(
    db: AsyncSession,
    *,
    context: CandidateContext,
    checksum: str,
) -> None:
    existing_checksums = tuple(
        (
            await db.scalars(
                select(MediaAsset.sha256).where(
                    MediaAsset.job_id == context.job.id,
                    MediaAsset.role.in_(
                        (MediaAssetRole.SOURCE.value, MediaAssetRole.CANDIDATE.value)
                    ),
                    MediaAsset.status == MediaAssetStatus.ACTIVE.value,
                )
            )
        ).all()
    )
    if checksum in set(existing_checksums):
        raise GenerationCandidateError("generation_candidate_duplicate_output")


async def persist_evolink_candidate(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    fact: EvolinkTaskFact,
    fetcher: Callable[[str], Awaitable[ValidatedImageBytes]] = fetch_admin_https,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> MediaAsset:
    """Persist intent, store privately, and activate only under a fresh fence."""

    current = now or datetime.now(timezone.utc)
    if fact.state is not EvolinkTaskState.SUCCEEDED or len(fact.output_urls) != 1:
        raise GenerationCandidateError("generation_candidate_provider_fact_invalid")
    context = await _load_candidate_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    if context.existing_asset is not None:
        return context.existing_asset
    if context.attempt.provider_job_id != fact.task_id:
        raise GenerationCandidateError("generation_candidate_provider_task_mismatch")

    validated = await fetcher(fact.output_urls[0])
    context = await _load_candidate_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    await _reject_source_duplicate(db, context=context, checksum=validated.sha256)
    candidate = await _load_or_create_candidate_intent(
        db,
        context=context,
        validated=validated,
        now=current,
    )

    # The intent commit released row locks, so revalidate immediately before I/O.
    await _load_candidate_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    try:
        await asyncio.to_thread(
            object_store.put_private,
            candidate.object_key,
            validated.content,
            validated.mime_type,
        )
    except FileExistsError:
        logger.debug("Generation candidate already exists; verifying exact stored bytes")
    stored = await asyncio.to_thread(object_store.read_private, candidate.object_key)
    if (
        len(stored) != validated.byte_size
        or hashlib.sha256(stored).hexdigest() != validated.sha256
    ):
        raise GenerationCandidateError("generation_candidate_store_integrity_failed")

    context = await _load_candidate_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    candidate = await _load_or_create_candidate_intent(
        db,
        context=context,
        validated=validated,
        now=current,
    )
    if MediaAssetStatus(candidate.status) is MediaAssetStatus.PENDING_UPLOAD:
        candidate.status = MediaAssetStatus.ACTIVE
    context.attempt.result_asset_id = candidate.id
    context.attempt.status = validate_attempt_transition(
        context.attempt.status,
        GenerationAttemptStatus.FINISHED,
    )
    context.attempt.finished_at = current
    context.order.status = OrderStatus.QA_PENDING
    await db.commit()
    return candidate
