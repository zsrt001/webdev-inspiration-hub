"""Durable, fenced deletion state machine for private MediaAsset objects."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_tombstone import AccountTombstone
from app.models.asset_access_grant import AssetAccessGrant
from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.live_portrait_job import LivePortraitJob
from app.models.media_asset import MediaAsset, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.models.partner_consent_case import (
    PartnerConsentCase,
    PartnerConsentCaseStatus,
)
from app.services.storage import DeleteResult, PrivateObjectStore, storage_service


LEASE_SECONDS = 120
REFERENCE_RECHECK_SECONDS = 300
_REASON_PATTERN = re.compile(r"^[a-z0-9_.-]{1,64}$")
_KNOWN_MEDIA_FK_CONSTRAINTS = {
    "media_assets_parent_asset_id_fkey",
    "asset_access_grants_asset_id_fkey",
    "live_portrait_jobs_source_asset_id_fkey",
    "live_portrait_jobs_video_asset_id_fkey",
    "generation_attempts_result_asset_id_fkey",
    "qa_verdicts_candidate_asset_id_fkey",
    "partner_invites_partner_asset_id_fkey",
}

logger = logging.getLogger(__name__)


class AssetDeletionError(RuntimeError):
    pass


class DeletionClaimError(AssetDeletionError):
    pass


@dataclass(frozen=True, slots=True)
class DeletionRequestResult:
    asset: MediaAsset
    code: str
    blockers: tuple[str, ...]


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("media deletion time must be timezone-aware")
    return current.astimezone(timezone.utc)


def _canonical_blockers(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip()[:128] for value in values if str(value).strip()}))


def _next_backoff(now: datetime, attempts: int) -> datetime:
    seconds = min(3600, 30 * (2 ** max(0, min(int(attempts) - 1, 7))))
    return now + timedelta(seconds=seconds)


def _clear_lease(asset: MediaAsset) -> None:
    asset.lease_owner = None
    asset.lease_claim_id = None
    asset.lease_expires_at = None


def _case_owned_asset_ids(case: PartnerConsentCase | object) -> tuple[uuid.UUID, ...] | None:
    values = getattr(case, "owned_asset_ids", None)
    if not isinstance(values, list) or not values:
        return None
    try:
        parsed = tuple(uuid.UUID(str(value)) for value in values)
    except (TypeError, ValueError, AttributeError):
        return None
    if len(set(parsed)) != len(parsed):
        return None
    return parsed


def partner_case_asset_deletion_authority(
    case: PartnerConsentCase | object,
    asset_id: uuid.UUID,
) -> tuple[str | None, bool]:
    """Return a blocker and whether settled case-owned references may be waived."""

    try:
        status = PartnerConsentCaseStatus(str(getattr(case.status, "value", case.status)))
    except (TypeError, ValueError, AttributeError):
        return "partner_consent_case_invalid", False
    owned_ids = _case_owned_asset_ids(case)
    if owned_ids is None:
        return "partner_consent_case_invalid", False
    if status is PartnerConsentCaseStatus.OPEN:
        return "partner_consent_case_open", False
    if status is PartnerConsentCaseStatus.SETTLED_DELETION_PENDING:
        if asset_id not in owned_ids:
            return "partner_consent_case_asset_not_owned", False
        return None, True
    return None, False


def partner_case_can_close(
    case: PartnerConsentCase | object,
    assets: list[MediaAsset | object],
) -> bool:
    if str(getattr(case.status, "value", case.status)) != PartnerConsentCaseStatus.SETTLED_DELETION_PENDING.value:
        return False
    owned_ids = _case_owned_asset_ids(case)
    if owned_ids is None:
        return False
    by_id = {asset.id: asset for asset in assets}
    return set(by_id) == set(owned_ids) and all(
        MediaAssetStatus(asset.status) is MediaAssetStatus.DELETED
        for asset in by_id.values()
    )


async def _partner_case_deletion_authority(
    db: AsyncSession,
    asset: MediaAsset,
) -> tuple[str | None, bool]:
    related = [PartnerConsentCase.owned_asset_ids.contains([str(asset.id)])]
    if asset.order_id is not None:
        related.append(PartnerConsentCase.order_id == asset.order_id)
    if asset.job_id is not None:
        related.append(PartnerConsentCase.job_id == asset.job_id)
    cases = list(
        (
            await db.scalars(
                select(PartnerConsentCase)
                .where(or_(*related))
                .order_by(PartnerConsentCase.id)
            )
        ).all()
    )
    if not cases:
        return None, False
    if len(cases) != 1:
        return "partner_consent_case_ambiguous", False
    return partner_case_asset_deletion_authority(cases[0], asset.id)


def generation_reference_blocker(
    *,
    job_status: GenerationJobStatus | str,
    attempt_statuses: list[GenerationAttemptStatus | str],
    settlement_status: str,
    delivery_status: str,
) -> str | None:
    """Fail closed until a generation graph is terminal and commercially settled."""

    normalized_job = (
        job_status.value if hasattr(job_status, "value") else str(job_status)
    )
    normalized_attempts = {
        item.value if hasattr(item, "value") else str(item) for item in attempt_statuses
    }
    if normalized_job in {
        GenerationJobStatus.QUEUED.value,
        GenerationJobStatus.ACTIVE.value,
        GenerationJobStatus.RECONCILING.value,
    }:
        return "generation_reference_unresolved"
    if normalized_attempts & {
        GenerationAttemptStatus.PREPARED.value,
        GenerationAttemptStatus.SUBMITTING.value,
        GenerationAttemptStatus.SUBMITTED.value,
        GenerationAttemptStatus.UNKNOWN.value,
    }:
        return "generation_reference_unresolved"
    settlement = str(settlement_status or "").upper()
    delivery = str(delivery_status or "").upper()
    if normalized_job == GenerationJobStatus.FINISHED.value:
        if (
            settlement == "CAPTURED"
            and delivery in {"PUBLISHED", "READY", "REVOKED", "DELETED"}
        ) or (
            settlement == "REFUNDED"
            and delivery in {"REVOKED", "DELETED"}
        ):
            return None
        return "generation_reference_unresolved"
    if normalized_job in {
        GenerationJobStatus.FAILED.value,
        GenerationJobStatus.CANCELLED.value,
    }:
        if settlement in {"RELEASED", "REFUNDED"} and delivery in {
            "NOT_DELIVERED",
            "REVOKED",
            "DELETED",
        }:
            return None
        return "generation_reference_unresolved"
    return "generation_reference_unresolved"


async def _generation_reference_blockers(
    db: AsyncSession,
    asset: MediaAsset,
) -> list[str]:
    order_reference = or_(
        Order.source_asset_ids.contains([str(asset.id)]),
        Order.preview_asset_ids.contains([str(asset.id)]),
        Order.final_asset_ids.contains([str(asset.id)]),
    )
    job_filter = order_reference
    if asset.job_id is not None:
        job_filter = or_(GenerationJob.id == asset.job_id, order_reference)
    jobs = list(
        (
            await db.scalars(
                select(GenerationJob)
                .join(Order, Order.id == GenerationJob.order_id)
                .where(job_filter)
                .order_by(GenerationJob.id)
            )
        ).all()
    )
    if asset.job_id is not None and not jobs:
        return ["generation_reference_unresolved"]
    if not jobs:
        return []
    attempts = list(
        (
            await db.scalars(
                select(GenerationAttempt)
                .where(GenerationAttempt.job_id.in_([job.id for job in jobs]))
                .order_by(GenerationAttempt.job_id, GenerationAttempt.attempt_number)
            )
        ).all()
    )
    attempts_by_job: dict[uuid.UUID, list[GenerationAttemptStatus | str]] = {}
    for attempt in attempts:
        attempts_by_job.setdefault(attempt.job_id, []).append(attempt.status)
    for job in jobs:
        if generation_reference_blocker(
            job_status=job.status,
            attempt_statuses=attempts_by_job.get(job.id, []),
            settlement_status=job.settlement_status,
            delivery_status=job.delivery_status,
        ):
            return ["generation_reference_unresolved"]
    return []


async def _lock_asset(db: AsyncSession, asset_id: uuid.UUID) -> MediaAsset:
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise AssetDeletionError("asset_not_found")
    return asset


async def _unknown_reference_constraints(db: AsyncSession) -> list[str]:
    result = await db.execute(
        text(
            """
            SELECT conname
              FROM pg_constraint
             WHERE contype = 'f'
               AND confrelid = 'public.media_assets'::regclass
             ORDER BY conname
            """
        )
    )
    return [
        f"unknown_reference_type:{name}"
        for name in result.scalars().all()
        if str(name) not in _KNOWN_MEDIA_FK_CONSTRAINTS
    ]


async def run_reference_guard(
    db: AsyncSession,
    asset: MediaAsset,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Resolve known active references; unknown future FK types fail closed."""

    current = _aware_now(now)
    blockers: list[str] = []
    case_blocker, waive_settled_references = await _partner_case_deletion_authority(
        db,
        asset,
    )
    if case_blocker is not None:
        blockers.append(case_blocker)
    blockers.extend(await _generation_reference_blockers(db, asset))

    grants = await db.execute(
        select(AssetAccessGrant.id)
        .where(
            AssetAccessGrant.asset_id == asset.id,
            AssetAccessGrant.revoked_at.is_(None),
            AssetAccessGrant.expires_at > current,
        )
        .limit(1)
    )
    if grants.scalar_one_or_none() is not None:
        blockers.append("active_provider_grant")

    children = await db.execute(
        select(MediaAsset.id)
        .where(
            MediaAsset.parent_asset_id == asset.id,
            MediaAsset.status != MediaAssetStatus.DELETED,
        )
        .limit(1)
    )
    if children.scalar_one_or_none() is not None:
        blockers.append("active_child_asset")

    orders = await db.execute(
        select(Order.status)
        .where(
            or_(
                Order.source_asset_ids.contains([str(asset.id)]),
                Order.preview_asset_ids.contains([str(asset.id)]),
                Order.final_asset_ids.contains([str(asset.id)]),
            )
        )
        .limit(1)
    )
    order_status = orders.scalar_one_or_none()
    if order_status is not None and OrderStatus(order_status) not in {
        OrderStatus.READY,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
        OrderStatus.COMPLETED,
        OrderStatus.DELETED,
    }:
        blockers.append("active_order_reference")

    live_portrait = await db.execute(
        select(LivePortraitJob.id)
        .where(
            or_(
                LivePortraitJob.source_asset_id == asset.id,
                LivePortraitJob.video_asset_id == asset.id,
            )
        )
        .limit(1)
    )
    if live_portrait.scalar_one_or_none() is not None:
        blockers.append("legacy_job_reference")

    blockers.extend(await _unknown_reference_constraints(db))
    if waive_settled_references:
        blockers = [
            blocker
            for blocker in blockers
            if blocker not in {"generation_reference_unresolved", "active_order_reference"}
        ]
    return list(_canonical_blockers(blockers))


async def _close_settled_partner_cases_for_asset(
    db: AsyncSession,
    *,
    asset_id: uuid.UUID,
    now: datetime,
) -> int:
    cases = list(
        (
            await db.scalars(
                select(PartnerConsentCase)
                .where(
                    PartnerConsentCase.status
                    == PartnerConsentCaseStatus.SETTLED_DELETION_PENDING.value,
                    PartnerConsentCase.owned_asset_ids.contains([str(asset_id)]),
                )
                .order_by(PartnerConsentCase.id)
                .with_for_update()
            )
        ).all()
    )
    closed = 0
    for case in cases:
        owned_ids = _case_owned_asset_ids(case)
        if owned_ids is None:
            continue
        assets = list(
            (
                await db.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.id.in_(owned_ids))
                    .order_by(MediaAsset.id)
                )
            ).all()
        )
        if partner_case_can_close(case, assets):
            case.status = PartnerConsentCaseStatus.CANCELLED_AND_DELETED
            case.closed_at = now
            case.version = int(case.version) + 1
            closed += 1
    return closed


async def request_asset_deletion(
    db: AsyncSession,
    asset_id: uuid.UUID,
    *,
    reason: str,
    now: datetime | None = None,
) -> DeletionRequestResult:
    current = _aware_now(now)
    clean_reason = str(reason or "").strip().lower()
    if not _REASON_PATTERN.fullmatch(clean_reason):
        raise ValueError("invalid media deletion reason")
    asset = await _lock_asset(db, asset_id)
    if MediaAssetStatus(asset.status) == MediaAssetStatus.DELETED:
        return DeletionRequestResult(asset=asset, code="already_deleted", blockers=())
    if MediaAssetStatus(asset.status) == MediaAssetStatus.PENDING_UPLOAD:
        raise AssetDeletionError("asset_upload_in_progress")

    asset.read_revoked_at = asset.read_revoked_at or current
    if MediaAssetStatus(asset.status) != MediaAssetStatus.PENDING_DELETE:
        asset.status = MediaAssetStatus.PENDING_DELETE
    asset.deletion_reason = clean_reason
    blockers = _canonical_blockers(await run_reference_guard(db, asset, now=current))
    asset.deletion_blockers = list(blockers)
    asset.next_delete_at = (
        current + timedelta(seconds=REFERENCE_RECHECK_SECONDS) if blockers else current
    )
    await db.commit()
    return DeletionRequestResult(
        asset=asset,
        code="active_reference" if blockers else "deletion_requested",
        blockers=blockers,
    )


async def claim_deletion_batch(
    db: AsyncSession,
    *,
    lease_owner: str,
    now: datetime | None = None,
    limit: int = 100,
) -> list[MediaAsset]:
    current = _aware_now(now)
    owner = str(lease_owner or "").strip()
    if not owner or len(owner) > 128:
        raise ValueError("invalid deletion lease owner")
    result = await db.execute(
        select(MediaAsset)
        .where(
            MediaAsset.status.in_(
                [MediaAssetStatus.PENDING_DELETE, MediaAssetStatus.DELETE_FAILED]
            ),
            MediaAsset.read_revoked_at.is_not(None),
            MediaAsset.next_delete_at.is_not(None),
            MediaAsset.next_delete_at <= current,
            or_(
                MediaAsset.deletion_blockers.is_(None),
                MediaAsset.deletion_blockers == [],
            ),
            or_(
                MediaAsset.lease_expires_at.is_(None),
                MediaAsset.lease_expires_at <= current,
            ),
        )
        .order_by(MediaAsset.next_delete_at.asc(), MediaAsset.id.asc())
        .limit(max(1, min(500, int(limit))))
        .with_for_update(skip_locked=True)
    )
    assets = list(result.scalars().all())
    for asset in assets:
        if MediaAssetStatus(asset.status) == MediaAssetStatus.DELETE_FAILED:
            asset.status = MediaAssetStatus.PENDING_DELETE
        asset.lease_owner = owner
        asset.lease_claim_id = uuid.uuid4()
        asset.lease_expires_at = current + timedelta(seconds=LEASE_SECONDS)
        asset.fencing_token = int(asset.fencing_token or 0) + 1
    await db.commit()
    return assets


async def recheck_blocked_deletions(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Re-run the guard before a previously blocked asset can be claimed."""

    current = _aware_now(now)
    result = await db.execute(
        select(MediaAsset)
        .where(
            MediaAsset.status.in_(
                [MediaAssetStatus.PENDING_DELETE, MediaAssetStatus.DELETE_FAILED]
            ),
            MediaAsset.read_revoked_at.is_not(None),
            MediaAsset.next_delete_at.is_not(None),
            MediaAsset.next_delete_at <= current,
            MediaAsset.deletion_blockers.is_not(None),
            MediaAsset.deletion_blockers != [],
        )
        .order_by(MediaAsset.next_delete_at.asc(), MediaAsset.id.asc())
        .limit(max(1, min(500, int(limit))))
        .with_for_update(skip_locked=True)
    )
    assets = list(result.scalars().all())
    for asset in assets:
        blockers = _canonical_blockers(
            await run_reference_guard(db, asset, now=current)
        )
        asset.deletion_blockers = list(blockers)
        asset.next_delete_at = (
            current + timedelta(seconds=REFERENCE_RECHECK_SECONDS)
            if blockers
            else current
        )
    await db.commit()
    return len(assets)


def require_current_deletion_claim(
    asset: MediaAsset,
    *,
    lease_claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime,
) -> None:
    if (
        asset.lease_claim_id != lease_claim_id
        or int(asset.fencing_token) != int(fencing_token)
        or asset.lease_expires_at is None
        or asset.lease_expires_at <= now
        or MediaAssetStatus(asset.status) != MediaAssetStatus.PENDING_DELETE
    ):
        raise DeletionClaimError("stale_or_expired_deletion_claim")


async def confirm_storage_deletion(
    db: AsyncSession,
    *,
    asset_id: uuid.UUID,
    lease_claim_id: uuid.UUID,
    fencing_token: int,
    result: DeleteResult,
    now: datetime | None = None,
) -> None:
    current = _aware_now(now)
    asset = await _lock_asset(db, asset_id)
    require_current_deletion_claim(
        asset,
        lease_claim_id=lease_claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    if result in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
        asset.status = MediaAssetStatus.DELETED
        asset.deleted_at = current
        asset.next_delete_at = None
        asset.last_delete_error = None
        await _close_settled_partner_cases_for_asset(
            db,
            asset_id=asset.id,
            now=current,
        )
    else:
        asset.status = MediaAssetStatus.DELETE_FAILED
        asset.delete_attempts = int(asset.delete_attempts or 0) + 1
        asset.last_delete_error = "private_store_delete_failed"
        asset.next_delete_at = _next_backoff(current, asset.delete_attempts)
    _clear_lease(asset)
    await db.commit()


async def run_claimed_deletion(
    db: AsyncSession,
    asset: MediaAsset,
    *,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> DeleteResult:
    claim_id = asset.lease_claim_id
    fence = int(asset.fencing_token)
    if claim_id is None:
        raise DeletionClaimError("missing_deletion_claim")
    try:
        result = await asyncio.to_thread(object_store.delete_private, asset.object_key)
    except Exception:
        logger.exception("Private object deletion failed for asset %s", asset.id)
        result = DeleteResult.FAILED
    await confirm_storage_deletion(
        db,
        asset_id=asset.id,
        lease_claim_id=claim_id,
        fencing_token=fence,
        result=result,
        now=now,
    )
    return result


async def reconcile_account_media_cleanup(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> int:
    """Clear tombstone cleanup markers only after every asset is DELETED."""

    result = await db.execute(
        select(AccountTombstone)
        .where(AccountTombstone.media_cleanup_pending.is_(True))
        .order_by(AccountTombstone.closed_at.asc())
        .limit(max(1, min(500, int(limit))))
        .with_for_update(skip_locked=True)
    )
    tombstones = list(result.scalars().all())
    reconciled = 0
    for tombstone in tombstones:
        remaining = await db.execute(
            select(MediaAsset.id)
            .where(
                MediaAsset.owner_user_id == tombstone.user_id,
                MediaAsset.status != MediaAssetStatus.DELETED,
            )
            .limit(1)
        )
        if remaining.scalar_one_or_none() is None:
            tombstone.media_cleanup_pending = False
            reconciled += 1
    await db.commit()
    return reconciled


async def run_deletion_cleanup(
    db: AsyncSession,
    *,
    lease_owner: str,
    now: datetime | None = None,
    limit: int = 100,
    object_store: PrivateObjectStore = storage_service,
) -> dict[str, int]:
    """Recheck blockers, claim with fencing, delete, and reconcile tombstones."""

    current = _aware_now(now)
    bounded_limit = max(1, min(500, int(limit)))
    rechecked = await recheck_blocked_deletions(
        db,
        now=current,
        limit=bounded_limit,
    )
    assets = await claim_deletion_batch(
        db,
        lease_owner=lease_owner,
        now=current,
        limit=bounded_limit,
    )
    summary = {
        "rechecked": rechecked,
        "claimed": len(assets),
        "deleted": 0,
        "not_found": 0,
        "failed": 0,
        "tombstones_reconciled": 0,
    }
    for asset in assets:
        try:
            result = await run_claimed_deletion(
                db,
                asset,
                object_store=object_store,
                now=current,
            )
        except Exception:
            summary["failed"] += 1
            logger.exception("Deletion cleanup failed after claiming asset %s", asset.id)
            continue
        if result == DeleteResult.DELETED:
            summary["deleted"] += 1
        elif result == DeleteResult.NOT_FOUND:
            summary["not_found"] += 1
        else:
            summary["failed"] += 1
    summary["tombstones_reconciled"] = await reconcile_account_media_cleanup(
        db,
        limit=bounded_limit,
    )
    return summary
