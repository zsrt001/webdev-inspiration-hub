"""Fenced private delivery artifacts, entitlement funding, and settlement."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
from typing import Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_reservation import (
    CreditReservation,
    CreditReservationAllocation,
    ReservationStatus,
)
from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.order_entitlement_funding import OrderEntitlementFunding
from app.models.qa_verdict import QaDecision, QaVerdict
from app.services.credit_reservation_service import FUNDING_POLICY_VERSION
from app.services.job_lease_service import (
    JobLease,
    complete_generation_job,
    require_current_generation_fence,
)
from app.services.postprocess_service import (
    PAID_VARIANT_RATIOS,
    RenderedPrivateArtifact,
    RenderedPrivateDeliverySet,
    ValidatedPrivateImage,
    render_private_delivery_set,
)
from app.services.partner_invite_service import require_partner_generation_allowed
from app.services.storage import DeleteResult, PrivateObjectStore, storage_service
from app.services.trial_access_service import (
    WatermarkedTrialImage,
    build_trial_watermark_bytes,
)


settings = get_settings()
DELIVERY_POLICY_VERSION = "private-delivery.v1"
PAID_ACCESS_TIER = "paid_download"
_DELIVERY_NAMESPACE = uuid.UUID("39cd48f1-19de-4809-bc6f-8d5c743daef9")
_ENTITLEMENT_NAMESPACE = uuid.UUID("d3ab790e-fe51-431b-ab83-5c54b7171966")
_RETENTION_DAYS = {
    "welcome_30d": 30,
    "free_30d": 30,
    "paid_90d": 90,
    "subscription_180d": 180,
    "studio_365d": 365,
}
_PAID_UNLOCK_TYPES = frozenset(
    {GrantLotSourceType.PURCHASE.value, GrantLotSourceType.SUBSCRIPTION.value}
)


class DeliverySettlementError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EntitlementFundingFact:
    allocation_id: uuid.UUID
    grant_lot_id: uuid.UUID
    root_transaction_id: uuid.UUID
    root_kind: str
    amount: int


@dataclass(frozen=True, slots=True)
class DeliveryArtifactPayload:
    asset: MediaAsset
    image_bytes: bytes


@dataclass(frozen=True, slots=True)
class PrivateDeliverySet:
    master: MediaAsset
    variants: tuple[MediaAsset, ...]
    preview: MediaAsset | None
    entitlement: OrderEntitlement | None

    @property
    def final_assets(self) -> tuple[MediaAsset, ...]:
        return (self.master, *self.variants)


@dataclass(frozen=True, slots=True)
class DeliveryContext:
    job: GenerationJob
    attempt: GenerationAttempt
    order: Order
    candidate: MediaAsset
    verdict: QaVerdict


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _aware(value: datetime, *, code: str) -> datetime:
    if value.tzinfo is None:
        raise DeliverySettlementError(code)
    return value.astimezone(timezone.utc)


def _moment(value: datetime | None) -> datetime:
    return _aware(value or datetime.now(timezone.utc), code="delivery_time_must_be_aware")


def is_trial_funding_snapshot(snapshot: object) -> bool:
    """Read trial authority only from the immutable order-funding contract."""

    if not isinstance(snapshot, dict) or snapshot.get("policy_version") != FUNDING_POLICY_VERSION:
        raise DeliverySettlementError("delivery_funding_policy_invalid")
    is_trial = snapshot.get("is_trial")
    lot_class = snapshot.get("allowed_lot_class")
    if not isinstance(is_trial, bool) or lot_class not in {"WELCOME_ONLY", "PAID_ONLY"}:
        raise DeliverySettlementError("delivery_funding_policy_invalid")
    if is_trial != (lot_class == "WELCOME_ONLY"):
        raise DeliverySettlementError("delivery_funding_policy_incoherent")
    return is_trial


def retention_deadline_for_tier(
    *,
    original_ready_at: datetime,
    retention_tier: str,
    existing_expires_at: datetime | None,
) -> datetime:
    ready_at = _aware(original_ready_at, code="delivery_ready_time_must_be_aware")
    days = _RETENTION_DAYS.get(str(retention_tier or ""))
    if days is None:
        raise DeliverySettlementError("delivery_retention_tier_unknown")
    desired = ready_at + timedelta(days=days)
    if existing_expires_at is None:
        return desired
    existing = _aware(existing_expires_at, code="delivery_expiry_must_be_aware")
    return max(existing, desired)


def validate_captured_entitlement_funding(
    *,
    reservation: CreditReservation | object,
    allocations: Sequence[CreditReservationAllocation | object],
    lots: Sequence[CreditGrantLot | object],
) -> tuple[EntitlementFundingFact, ...]:
    """Prove an entitlement can reproduce one captured reservation exactly."""

    if _value(getattr(reservation, "status", "")) != ReservationStatus.CAPTURED.value:
        raise DeliverySettlementError("delivery_reservation_not_captured")
    if getattr(reservation, "captured_transaction_id", None) is None:
        raise DeliverySettlementError("delivery_capture_transaction_missing")
    ordered_allocations = sorted(allocations, key=lambda item: str(item.id))
    if not ordered_allocations or any(
        item.reservation_id != reservation.id or int(item.amount or 0) <= 0
        for item in ordered_allocations
    ):
        raise DeliverySettlementError("delivery_funding_allocation_invalid")
    if sum(int(item.amount) for item in ordered_allocations) != int(reservation.amount):
        raise DeliverySettlementError("delivery_funding_allocation_mismatch")
    lots_by_id = {item.id: item for item in lots}
    if set(lots_by_id) != {item.grant_lot_id for item in ordered_allocations}:
        raise DeliverySettlementError("delivery_funding_lot_mismatch")

    facts: list[EntitlementFundingFact] = []
    for allocation in ordered_allocations:
        lot = lots_by_id[allocation.grant_lot_id]
        if lot.user_id != reservation.user_id:
            raise DeliverySettlementError("delivery_funding_owner_mismatch")
        if int(lot.reversed_amount or 0) or int(lot.frozen_amount or 0):
            raise DeliverySettlementError("delivery_funding_reversed_or_frozen")
        root_id = getattr(lot, "root_transaction_id", None)
        if not isinstance(root_id, uuid.UUID):
            raise DeliverySettlementError("delivery_funding_root_missing")
        facts.append(
            EntitlementFundingFact(
                allocation_id=allocation.id,
                grant_lot_id=lot.id,
                root_transaction_id=root_id,
                root_kind=_value(lot.source_type),
                amount=int(allocation.amount),
            )
        )
    return tuple(facts)


def validate_trial_unlock_grant(
    lot: CreditGrantLot | object,
    *,
    user_id: uuid.UUID,
    now: datetime,
) -> None:
    current = _aware(now, code="trial_unlock_time_must_be_aware")
    if getattr(lot, "user_id", None) != user_id:
        raise DeliverySettlementError("trial_unlock_grant_owner_mismatch")
    if _value(getattr(lot, "source_type", "")) not in _PAID_UNLOCK_TYPES:
        raise DeliverySettlementError("trial_unlock_grant_type_invalid")
    expires_at = getattr(lot, "expires_at", None)
    if expires_at is not None and _aware(expires_at, code="trial_unlock_expiry_invalid") <= current:
        raise DeliverySettlementError("trial_unlock_grant_expired")
    spendable = max(
        0,
        int(lot.original_amount or 0)
        - int(lot.debt_offset_amount or 0)
        - int(lot.reversed_amount or 0)
        - int(lot.frozen_amount or 0)
        - int(lot.consumed_amount or 0),
    )
    if int(lot.reversed_amount or 0) or int(lot.frozen_amount or 0) or spendable <= 0:
        raise DeliverySettlementError("trial_unlock_grant_inactive")
    if not isinstance(getattr(lot, "root_transaction_id", None), uuid.UUID):
        raise DeliverySettlementError("trial_unlock_grant_root_missing")


def delivery_asset_id(job_id: uuid.UUID, name: str) -> uuid.UUID:
    return uuid.uuid5(_DELIVERY_NAMESPACE, f"{job_id}:{name}")


def _delivery_object_key(
    *, owner_user_id: uuid.UUID, job_id: uuid.UUID, asset_id: uuid.UUID, name: str
) -> str:
    safe_name = str(name).lower().replace(":", "x").replace("_", "-")
    return (
        f"users/{owner_user_id}/generation/{job_id}/delivery/"
        f"{asset_id}-{safe_name}.jpg"
    )


async def _load_delivery_context(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    now: datetime,
) -> DeliveryContext:
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
        or _value(attempt.status) != GenerationAttemptStatus.FINISHED.value
        or attempt.result_asset_id is None
    ):
        raise DeliverySettlementError("delivery_attempt_invalid")
    order = await db.scalar(select(Order).where(Order.id == job.order_id).with_for_update())
    if (
        order is None
        or order.generation_job_id != job.id
        or order.reservation_id is None
        or order.deleted_at is not None
    ):
        raise DeliverySettlementError("delivery_order_invalid")
    product = order.product_policy_snapshot
    if not isinstance(product, dict) or product.get("policy_version") != "order-policy.v1":
        raise DeliverySettlementError("delivery_order_policy_invalid")
    if str(product.get("generation_mode") or "").strip().lower() == "partner_invite":
        raise DeliverySettlementError("delivery_partner_consent_not_available")
    candidate = await db.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == attempt.result_asset_id)
        .with_for_update()
    )
    if (
        candidate is None
        or candidate.owner_user_id != order.user_id
        or candidate.order_id != order.id
        or candidate.job_id != job.id
        or _value(candidate.role) != MediaAssetRole.CANDIDATE.value
        or _value(candidate.status) != MediaAssetStatus.ACTIVE.value
        or candidate.read_revoked_at is not None
        or candidate.expires_at <= now
    ):
        raise DeliverySettlementError("delivery_candidate_invalid")
    verdict = await db.scalar(
        select(QaVerdict).where(
            QaVerdict.job_id == job.id,
            QaVerdict.attempt_id == attempt.id,
            QaVerdict.candidate_asset_id == candidate.id,
        )
    )
    if verdict is None or _value(verdict.decision) != QaDecision.PASS.value:
        raise DeliverySettlementError("delivery_passing_verdict_missing")
    return DeliveryContext(job=job, attempt=attempt, order=order, candidate=candidate, verdict=verdict)


def _artifact_asset(
    *,
    context: DeliveryContext,
    artifact: RenderedPrivateArtifact | WatermarkedTrialImage,
    name: str,
    role: MediaAssetRole,
    parent_asset_id: uuid.UUID,
    expires_at: datetime,
) -> MediaAsset:
    asset_id = delivery_asset_id(context.job.id, name)
    return MediaAsset(
        id=asset_id,
        owner_user_id=context.order.user_id,
        order_id=context.order.id,
        job_id=context.job.id,
        parent_asset_id=parent_asset_id,
        role=role,
        storage_provider=settings.effective_storage_provider,
        object_key=_delivery_object_key(
            owner_user_id=context.order.user_id,
            job_id=context.job.id,
            asset_id=asset_id,
            name=name,
        ),
        sha256=artifact.sha256,
        mime_type=artifact.mime_type,
        byte_size=len(artifact.image_bytes),
        width=artifact.width,
        height=artifact.height,
        access_level="private",
        policy_version=DELIVERY_POLICY_VERSION,
        expires_at=expires_at,
        status=MediaAssetStatus.PENDING_UPLOAD,
    )


def _intent_matches(actual: MediaAsset, expected: MediaAsset) -> bool:
    fields = (
        "owner_user_id",
        "order_id",
        "job_id",
        "parent_asset_id",
        "object_key",
        "sha256",
        "mime_type",
        "byte_size",
        "width",
        "height",
        "policy_version",
    )
    return (
        all(getattr(actual, field) == getattr(expected, field) for field in fields)
        and _value(actual.role) == _value(expected.role)
        and _value(actual.status)
        in {MediaAssetStatus.PENDING_UPLOAD.value, MediaAssetStatus.ACTIVE.value}
        and actual.read_revoked_at is None
    )


async def _persist_all_intents(
    db: AsyncSession,
    payloads: Sequence[DeliveryArtifactPayload],
) -> tuple[DeliveryArtifactPayload, ...]:
    ids = [item.asset.id for item in payloads]
    existing = list(
        (
            await db.scalars(
                select(MediaAsset).where(MediaAsset.id.in_(ids)).with_for_update()
            )
        ).all()
    )
    by_id = {item.id: item for item in existing}
    durable: list[DeliveryArtifactPayload] = []
    for payload in payloads:
        asset = by_id.get(payload.asset.id)
        if asset is None:
            asset = payload.asset
            db.add(asset)
        elif not _intent_matches(asset, payload.asset):
            raise DeliverySettlementError("delivery_asset_intent_conflict")
        durable.append(DeliveryArtifactPayload(asset=asset, image_bytes=payload.image_bytes))
    await db.flush()
    await db.commit()
    return tuple(durable)


def _validate_stored_image(asset: MediaAsset, payload: bytes) -> None:
    from PIL import Image

    content = bytes(payload)
    if (
        not content
        or len(content) != int(asset.byte_size)
        or hashlib.sha256(content).hexdigest() != asset.sha256
    ):
        raise DeliverySettlementError("delivery_object_integrity_failed")
    try:
        with Image.open(BytesIO(content)) as decoded:
            decoded.verify()
            size = decoded.size
            image_format = decoded.format
    except Exception as exc:
        raise DeliverySettlementError("delivery_object_decode_failed") from exc
    if image_format != "JPEG" or size != (asset.width, asset.height):
        raise DeliverySettlementError("delivery_object_technical_qa_failed")


async def _store_one_artifact(
    db: AsyncSession,
    *,
    payload: DeliveryArtifactPayload,
    attempt_id: uuid.UUID,
    lease: JobLease,
    object_store: PrivateObjectStore,
    now: datetime | None,
) -> None:
    context = await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    _ = context
    # Release row locks before storage I/O; the same full fence is re-locked below.
    await db.commit()
    try:
        await asyncio.to_thread(
            object_store.put_private,
            payload.asset.object_key,
            payload.image_bytes,
            payload.asset.mime_type,
        )
    except FileExistsError:
        pass
    stored = await asyncio.to_thread(object_store.read_private, payload.asset.object_key)
    await asyncio.to_thread(_validate_stored_image, payload.asset, stored)
    await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )


async def _delete_untracked_delivery_objects(
    db: AsyncSession,
    *,
    context: DeliveryContext,
    payloads: Sequence[DeliveryArtifactPayload],
    attempt_id: uuid.UUID,
    lease: JobLease,
    object_store: PrivateObjectStore,
    now: datetime | None,
) -> None:
    """Remove only objects inside this exact job prefix that lack a durable intent."""

    prefix = f"users/{context.order.user_id}/generation/{context.job.id}/delivery/"
    expected = {item.asset.object_key for item in payloads}
    await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    await db.commit()
    listed = await asyncio.to_thread(object_store.list_private, prefix, limit=32)
    if any(not key.startswith(prefix) for key in listed):
        raise DeliverySettlementError("delivery_object_listing_escaped_prefix")
    await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    for object_key in sorted(set(listed) - expected):
        await db.commit()
        result = await asyncio.to_thread(object_store.delete_private, object_key)
        if result not in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
            raise DeliverySettlementError("delivery_orphan_delete_failed")
        await _load_delivery_context(
            db, attempt_id=attempt_id, lease=lease, now=_moment(now)
        )


async def _lock_reservation_funding(
    db: AsyncSession,
    reservation_id: uuid.UUID,
) -> tuple[CreditReservation, list[CreditReservationAllocation], list[CreditGrantLot]]:
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise DeliverySettlementError("delivery_reservation_missing")
    allocations = list(
        (
            await db.scalars(
                select(CreditReservationAllocation)
                .where(CreditReservationAllocation.reservation_id == reservation.id)
                .order_by(CreditReservationAllocation.id)
                .with_for_update()
            )
        ).all()
    )
    lots = list(
        (
            await db.scalars(
                select(CreditGrantLot)
                .where(CreditGrantLot.id.in_([item.grant_lot_id for item in allocations]))
                .order_by(CreditGrantLot.id)
                .with_for_update()
            )
        ).all()
    )
    return reservation, allocations, lots


async def _create_or_validate_paid_entitlement(
    db: AsyncSession,
    *,
    context: DeliveryContext,
    reservation: CreditReservation,
    allocations: Sequence[CreditReservationAllocation],
    lots: Sequence[CreditGrantLot],
    expires_at: datetime,
) -> OrderEntitlement:
    facts = validate_captured_entitlement_funding(
        reservation=reservation, allocations=allocations, lots=lots
    )
    if any(item.root_kind == GrantLotSourceType.WELCOME.value for item in facts):
        raise DeliverySettlementError("paid_delivery_welcome_funding_forbidden")
    existing = await db.scalar(
        select(OrderEntitlement)
        .where(OrderEntitlement.order_id == context.order.id)
        .with_for_update()
    )
    if existing is None:
        existing = OrderEntitlement(
            id=uuid.uuid5(_ENTITLEMENT_NAMESPACE, str(context.order.id)),
            order_id=context.order.id,
            user_id=context.order.user_id,
            reservation_id=reservation.id,
            status=EntitlementStatus.ACTIVE,
            access_tier=PAID_ACCESS_TIER,
            retention_tier=str(reservation.captured_retention_tier),
            expires_at=expires_at,
        )
        db.add(existing)
        await db.flush()
        for fact in facts:
            db.add(
                OrderEntitlementFunding(
                    id=uuid.uuid5(existing.id, str(fact.allocation_id)),
                    entitlement_id=existing.id,
                    reservation_allocation_id=fact.allocation_id,
                    grant_lot_id=fact.grant_lot_id,
                    amount=fact.amount,
                )
            )
        await db.flush()
        return existing
    if (
        existing.user_id != context.order.user_id
        or existing.reservation_id != reservation.id
        or existing.unlock_grant_lot_id is not None
        or existing.unlock_root_transaction_id is not None
        or _value(existing.status) != EntitlementStatus.ACTIVE.value
        or existing.access_tier != PAID_ACCESS_TIER
    ):
        raise DeliverySettlementError("delivery_entitlement_replay_conflict")
    rows = list(
        (
            await db.scalars(
                select(OrderEntitlementFunding)
                .where(OrderEntitlementFunding.entitlement_id == existing.id)
                .order_by(OrderEntitlementFunding.reservation_allocation_id)
            )
        ).all()
    )
    expected = {
        (item.allocation_id, item.grant_lot_id, item.amount) for item in facts
    }
    actual = {
        (item.reservation_allocation_id, item.grant_lot_id, int(item.amount))
        for item in rows
    }
    if actual != expected:
        raise DeliverySettlementError("delivery_entitlement_funding_replay_conflict")
    if existing.expires_at < expires_at:
        raise DeliverySettlementError("delivery_entitlement_retention_replay_conflict")
    return existing


def _build_payloads(
    *,
    context: DeliveryContext,
    rendered: RenderedPrivateDeliverySet,
    watermarked: WatermarkedTrialImage | None,
    expires_at: datetime,
) -> tuple[DeliveryArtifactPayload, ...]:
    master = _artifact_asset(
        context=context,
        artifact=rendered.master,
        name=rendered.master.name,
        role=MediaAssetRole.FINAL_MASTER,
        parent_asset_id=context.candidate.id,
        expires_at=expires_at,
    )
    payloads = [
        DeliveryArtifactPayload(asset=master, image_bytes=rendered.master.image_bytes)
    ]
    for ratio in PAID_VARIANT_RATIOS:
        artifact = rendered.variants[ratio]
        payloads.append(
            DeliveryArtifactPayload(
                asset=_artifact_asset(
                    context=context,
                    artifact=artifact,
                    name=f"variant_{ratio}",
                    role=MediaAssetRole.DELIVERY_VARIANT,
                    parent_asset_id=master.id,
                    expires_at=expires_at,
                ),
                image_bytes=artifact.image_bytes,
            )
        )
    if watermarked is not None:
        payloads.append(
            DeliveryArtifactPayload(
                asset=_artifact_asset(
                    context=context,
                    artifact=watermarked,
                    name="trial_preview_3x4",
                    role=MediaAssetRole.PREVIEW_WATERMARKED,
                    parent_asset_id=master.id,
                    expires_at=expires_at,
                ),
                image_bytes=watermarked.image_bytes,
            )
        )
    return tuple(payloads)


async def build_delivery_assets(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> PrivateDeliverySet:
    """Persist, verify, authorize, and publish one complete private delivery set."""

    context = await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    trial = is_trial_funding_snapshot(context.order.funding_policy_snapshot)
    # Candidate/object processing can be slow. Release the current row locks,
    # then re-lock and validate the full fence after each external/CPU boundary.
    await db.commit()
    candidate_bytes = await asyncio.to_thread(
        object_store.read_private, context.candidate.object_key
    )
    if (
        len(candidate_bytes) != int(context.candidate.byte_size)
        or hashlib.sha256(candidate_bytes).hexdigest() != context.candidate.sha256
    ):
        raise DeliverySettlementError("delivery_candidate_integrity_failed")
    context = await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    private_candidate = ValidatedPrivateImage(
        asset_id=context.candidate.id,
        image_bytes=candidate_bytes,
        mime_type=context.candidate.mime_type,
        sha256=context.candidate.sha256,
    )
    await db.commit()
    rendered = await asyncio.to_thread(
        render_private_delivery_set,
        private_candidate,
        template_id=context.order.template_id,
    )
    context = await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    master_id = delivery_asset_id(context.job.id, rendered.master.name)
    watermarked = None
    if trial:
        master_candidate = ValidatedPrivateImage(
            asset_id=master_id,
            image_bytes=rendered.master.image_bytes,
            mime_type=rendered.master.mime_type,
            sha256=rendered.master.sha256,
        )
        await db.commit()
        watermarked = await asyncio.to_thread(build_trial_watermark_bytes, master_candidate)
        context = await _load_delivery_context(
            db, attempt_id=attempt_id, lease=lease, now=_moment(now)
        )

    reservation, allocations, lots = await _lock_reservation_funding(
        db, context.order.reservation_id
    )
    facts = validate_captured_entitlement_funding(
        reservation=reservation, allocations=allocations, lots=lots
    )
    if reservation.order_id != context.order.id or reservation.user_id != context.order.user_id:
        raise DeliverySettlementError("delivery_reservation_lineage_mismatch")
    if trial and any(item.root_kind != GrantLotSourceType.WELCOME.value for item in facts):
        raise DeliverySettlementError("trial_delivery_non_welcome_funding")
    ready_at = context.job.finished_at or _moment(now)
    retention_tier = str(reservation.captured_retention_tier or "")
    expires_at = retention_deadline_for_tier(
        original_ready_at=ready_at,
        retention_tier=retention_tier,
        existing_expires_at=context.order.expires_at,
    )
    payloads = _build_payloads(
        context=context,
        rendered=rendered,
        watermarked=watermarked,
        expires_at=expires_at,
    )
    payloads = await _persist_all_intents(db, payloads)
    await _delete_untracked_delivery_objects(
        db,
        context=context,
        payloads=payloads,
        attempt_id=attempt_id,
        lease=lease,
        object_store=object_store,
        now=now,
    )
    for payload in payloads:
        await _store_one_artifact(
            db,
            payload=payload,
            attempt_id=attempt_id,
            lease=lease,
            object_store=object_store,
            now=now,
        )

    context = await _load_delivery_context(
        db, attempt_id=attempt_id, lease=lease, now=_moment(now)
    )
    reservation, allocations, lots = await _lock_reservation_funding(
        db, context.order.reservation_id
    )
    validate_captured_entitlement_funding(
        reservation=reservation, allocations=allocations, lots=lots
    )
    if reservation.order_id != context.order.id or reservation.user_id != context.order.user_id:
        raise DeliverySettlementError("delivery_reservation_lineage_mismatch")
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_([item.asset.id for item in payloads]))
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {item.id: item for item in assets}
    if set(by_id) != {item.asset.id for item in payloads}:
        raise DeliverySettlementError("delivery_asset_set_incomplete")
    for payload in payloads:
        asset = by_id[payload.asset.id]
        if not _intent_matches(asset, payload.asset):
            raise DeliverySettlementError("delivery_asset_activation_conflict")
        asset.status = MediaAssetStatus.ACTIVE
        asset.expires_at = max(asset.expires_at, expires_at)

    master = by_id[delivery_asset_id(context.job.id, rendered.master.name)]
    variants = tuple(
        by_id[delivery_asset_id(context.job.id, f"variant_{ratio}")]
        for ratio in PAID_VARIANT_RATIOS
    )
    preview = (
        by_id[delivery_asset_id(context.job.id, "trial_preview_3x4")] if trial else None
    )
    entitlement = None
    if not trial:
        entitlement = await _create_or_validate_paid_entitlement(
            db,
            context=context,
            reservation=reservation,
            allocations=allocations,
            lots=lots,
            expires_at=expires_at,
        )
    context.order.preview_asset_ids = [str(preview.id)] if preview else None
    context.order.final_asset_ids = [str(master.id), *[str(item.id) for item in variants]]
    context.order.source_image_urls = None
    context.order.preview_image_urls = None
    context.order.final_image_urls = None
    context.order.expires_at = expires_at
    context.order.status = OrderStatus.READY
    context.order.settlement_status = "CAPTURED"
    context.order.delivery_status = "READY"
    context.job.settlement_status = "CAPTURED"
    context.job.delivery_status = "READY"
    await complete_generation_job(
        db,
        job_id=context.job.id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        terminal_status=GenerationJobStatus.FINISHED,
        now=ready_at,
    )
    await db.commit()
    return PrivateDeliverySet(
        master=master,
        variants=variants,
        preview=preview,
        entitlement=entitlement,
    )


async def prepare_delivery_intents_for_terminal_cleanup(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    now: datetime | None = None,
) -> tuple[uuid.UUID, ...]:
    """Stage failed objects for deletion in the same transaction as settlement."""

    current = _moment(now)
    await _load_delivery_context(db, attempt_id=attempt_id, lease=lease, now=current)
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(
                    MediaAsset.job_id == lease.job_id,
                    MediaAsset.policy_version == DELIVERY_POLICY_VERSION,
                    MediaAsset.status.in_(
                        (MediaAssetStatus.PENDING_UPLOAD, MediaAssetStatus.ACTIVE)
                    ),
                )
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    for asset in assets:
        status = MediaAssetStatus(asset.status)
        if status is MediaAssetStatus.PENDING_UPLOAD:
            asset.status = MediaAssetStatus.UPLOAD_FAILED
        elif status is MediaAssetStatus.ACTIVE:
            asset.status = MediaAssetStatus.QUARANTINED
        asset.read_revoked_at = asset.read_revoked_at or current
    await db.flush()
    for asset in assets:
        asset.status = MediaAssetStatus.PENDING_DELETE
        asset.deletion_reason = "delivery_failed"
        asset.deletion_blockers = []
        asset.next_delete_at = current
    await db.flush()
    return tuple(asset.id for asset in assets)
