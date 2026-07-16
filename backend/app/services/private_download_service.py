"""Owner-only private order projection, entitlement unlock, and byte streaming."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_reservation import (
    CreditReservation,
    CreditReservationAllocation,
    ReservationStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.order_entitlement_funding import OrderEntitlementFunding
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.payment_reconciliation_case import (
    PaymentReconciliationCase,
    ReconciliationCaseStatus,
)
from app.models.partner_consent_case import PartnerConsentCase
from app.models.partner_invite import PartnerInvite, PartnerInviteStatus
from app.schemas.order import (
    OrderAssetRead,
    OrderFundingAllocationRead,
    OrderFundingRead,
    TrialUnlockRead,
)
from app.services.delivery_asset_service import (
    DeliverySettlementError,
    is_trial_funding_snapshot,
    retention_deadline_for_tier,
    validate_captured_entitlement_funding,
    validate_trial_unlock_grant,
)
from app.services.idempotency_service import (
    IdempotencyConflict,
    begin_idempotent_request,
    canonical_request_hash,
    complete_idempotent_request,
)
from app.services.storage import PrivateObjectStore, storage_service


TRIAL_UNLOCKED_ACCESS_TIER = "trial_unlocked"
_VISIBLE_ROLES = frozenset(
    {
        MediaAssetRole.PREVIEW_WATERMARKED.value,
        MediaAssetRole.FINAL_MASTER.value,
        MediaAssetRole.DELIVERY_VARIANT.value,
    }
)


class PrivateDownloadError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PrivateDownloadResult:
    asset_id: uuid.UUID
    content: bytes
    mime_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class OrderAssetProjection:
    assets: tuple[OrderAssetRead, ...]
    can_download: bool
    entitlement_status: str | None
    access_tier: str | None


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _aware(value: datetime, code: str) -> datetime:
    if value.tzinfo is None:
        raise PrivateDownloadError(code)
    return value.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    return _aware(value or datetime.now(timezone.utc), "private_download_time_invalid")


def _validate_bound_unlock_lot(
    entitlement: OrderEntitlement | object,
    lot: CreditGrantLot | object | None,
    *,
    user_id: uuid.UUID,
) -> None:
    grant_id = getattr(entitlement, "unlock_grant_lot_id", None)
    root_id = getattr(entitlement, "unlock_root_transaction_id", None)
    if (grant_id is None) != (root_id is None):
        raise PrivateDownloadError("private_download_unlock_lineage_incoherent")
    if grant_id is None:
        if lot is not None:
            raise PrivateDownloadError("private_download_unlock_lineage_unexpected")
        return
    if (
        lot is None
        or lot.id != grant_id
        or lot.user_id != user_id
        or lot.root_transaction_id != root_id
        or _value(lot.source_type)
        not in {GrantLotSourceType.PURCHASE.value, GrantLotSourceType.SUBSCRIPTION.value}
    ):
        raise PrivateDownloadError("private_download_unlock_lineage_invalid")
    if int(lot.reversed_amount or 0) or int(lot.frozen_amount or 0):
        raise PrivateDownloadError("private_download_unlock_revoked_or_frozen")


def validate_entitlement_download_authority(
    *,
    entitlement: OrderEntitlement | object,
    reservation: CreditReservation | object,
    allocations: list[CreditReservationAllocation | object],
    funding_rows: list[OrderEntitlementFunding | object],
    lots: list[CreditGrantLot | object],
    unlock_lot: CreditGrantLot | object | None,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> None:
    current = _aware(now, "private_download_time_invalid")
    if (
        entitlement.order_id != order_id
        or entitlement.user_id != user_id
        or entitlement.reservation_id != reservation.id
    ):
        raise PrivateDownloadError("private_download_entitlement_lineage_invalid")
    if _value(entitlement.status) != EntitlementStatus.ACTIVE.value:
        raise PrivateDownloadError("private_download_entitlement_inactive", status_code=403)
    if _aware(entitlement.expires_at, "private_download_entitlement_expiry_invalid") <= current:
        raise PrivateDownloadError("private_download_entitlement_expired", status_code=403)
    if reservation.order_id != order_id or reservation.user_id != user_id:
        raise PrivateDownloadError("private_download_reservation_lineage_invalid")
    try:
        facts = validate_captured_entitlement_funding(
            reservation=reservation,
            allocations=allocations,
            lots=lots,
        )
    except DeliverySettlementError as exc:
        raise PrivateDownloadError(f"private_download_{exc.code}", status_code=403) from exc
    expected = {
        (item.allocation_id, item.grant_lot_id, item.amount) for item in facts
    }
    actual = {
        (
            item.reservation_allocation_id,
            item.grant_lot_id,
            int(item.amount),
        )
        for item in funding_rows
        if item.entitlement_id == entitlement.id
    }
    if actual != expected or len(actual) != len(funding_rows):
        raise PrivateDownloadError("private_download_entitlement_funding_invalid", status_code=403)
    _validate_bound_unlock_lot(entitlement, unlock_lot, user_id=user_id)


def _asset_ids(values: list[str] | None) -> tuple[uuid.UUID, ...]:
    try:
        parsed = tuple(uuid.UUID(str(value)) for value in (values or []))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PrivateDownloadError("private_download_asset_lineage_invalid") from exc
    if len(set(parsed)) != len(parsed):
        raise PrivateDownloadError("private_download_asset_lineage_invalid")
    return parsed


async def _lock_owned_order(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Order:
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None or order.user_id != user_id or order.deleted_at is not None:
        raise PrivateDownloadError("order_not_found", status_code=404)
    return order


async def _lock_funding_graph(
    db: AsyncSession,
    *,
    order: Order,
    require_entitlement: bool,
) -> tuple[
    CreditReservation,
    list[CreditReservationAllocation],
    list[CreditGrantLot],
    OrderEntitlement | None,
    list[OrderEntitlementFunding],
    CreditGrantLot | None,
]:
    if order.reservation_id is None:
        raise PrivateDownloadError("private_download_reservation_missing")
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == order.reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise PrivateDownloadError("private_download_reservation_missing")
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
    entitlement = await db.scalar(
        select(OrderEntitlement)
        .where(OrderEntitlement.order_id == order.id)
        .with_for_update()
    )
    if require_entitlement and entitlement is None:
        raise PrivateDownloadError("private_download_entitlement_missing", status_code=403)
    funding_rows: list[OrderEntitlementFunding] = []
    unlock_lot = None
    if entitlement is not None:
        funding_rows = list(
            (
                await db.scalars(
                    select(OrderEntitlementFunding)
                    .where(OrderEntitlementFunding.entitlement_id == entitlement.id)
                    .order_by(OrderEntitlementFunding.reservation_allocation_id)
                    .with_for_update()
                )
            ).all()
        )
        if entitlement.unlock_grant_lot_id is not None:
            unlock_lot = await db.scalar(
                select(CreditGrantLot)
                .where(CreditGrantLot.id == entitlement.unlock_grant_lot_id)
                .with_for_update()
            )
        lineage_lots = [*lots, *([unlock_lot] if unlock_lot is not None else [])]
        reconciliation_subjects = []
        for lot in lineage_lots:
            source_type = _value(lot.source_type)
            if source_type == GrantLotSourceType.PURCHASE.value:
                reconciliation_subjects.append(
                    (
                        PaymentReconciliationCase.subject_type == "credit_purchase",
                        PaymentReconciliationCase.subject_id == str(lot.source_id),
                    )
                )
            elif source_type == GrantLotSourceType.SUBSCRIPTION.value:
                reconciliation_subjects.append(
                    (
                        PaymentReconciliationCase.subject_type == "subscription_invoice",
                        PaymentReconciliationCase.subject_id == str(lot.source_id),
                    )
                )
        if reconciliation_subjects:
            unresolved = await db.scalar(
                select(PaymentReconciliationCase.id)
                .where(
                    or_(
                        *(
                            left & right
                            for left, right in reconciliation_subjects
                        )
                    ),
                    PaymentReconciliationCase.status
                    != ReconciliationCaseStatus.RESOLVED.value,
                )
                .limit(1)
                .with_for_update()
            )
            if unresolved is not None:
                raise PrivateDownloadError(
                    "private_download_payment_reconciliation_open",
                    status_code=403,
                )
    return reservation, allocations, lots, entitlement, funding_rows, unlock_lot


async def _require_delivery_order_authority(db: AsyncSession, order: Order) -> None:
    policy = order.product_policy_snapshot
    if not isinstance(policy, dict) or policy.get("policy_version") != "order-policy.v1":
        raise PrivateDownloadError("private_download_order_policy_invalid", status_code=403)
    invite = await db.scalar(
        select(PartnerInvite).where(PartnerInvite.order_id == order.id)
    )
    if invite is None:
        if str(policy.get("generation_mode") or "").strip().lower() == "partner_invite":
            raise PrivateDownloadError(
                "private_download_partner_consent_invalid",
                status_code=403,
            )
        return
    if (
        _value(invite.status) != PartnerInviteStatus.COMPLETED.value
        or invite.host_user_id != order.user_id
        or invite.partner_user_id is None
        or invite.partner_asset_id is None
        or invite.consent_event_id is None
        or invite.order_id != order.id
        or invite.job_id != order.generation_job_id
    ):
        raise PrivateDownloadError(
            "private_download_partner_consent_invalid",
            status_code=403,
        )
    consent_case = await db.scalar(
        select(PartnerConsentCase.id)
        .where(PartnerConsentCase.invite_id == invite.id)
        .limit(1)
    )
    if consent_case is not None:
        raise PrivateDownloadError("private_download_partner_consent_invalid", status_code=403)


async def project_order_assets(
    db: AsyncSession,
    *,
    order: Order,
    private_download_allowed: bool,
    now: datetime | None = None,
) -> OrderAssetProjection:
    """Build a public projection from active private assets and current entitlement."""

    current = _now(now)
    if not private_download_allowed or _value(order.status) != OrderStatus.READY.value:
        return OrderAssetProjection(assets=(), can_download=False, entitlement_status=None, access_tier=None)
    await _require_delivery_order_authority(db, order)
    preview_ids = _asset_ids(order.preview_asset_ids)
    final_ids = _asset_ids(order.final_asset_ids)
    reservation, allocations, lots, entitlement, funding_rows, unlock_lot = await _lock_funding_graph(
        db, order=order, require_entitlement=False
    )
    can_download = False
    entitlement_status = None
    access_tier = None
    if entitlement is not None:
        entitlement_status = _value(entitlement.status)
        access_tier = entitlement.access_tier
        if (
            entitlement_status == EntitlementStatus.ACTIVE.value
            and entitlement.expires_at > current
        ):
            validate_entitlement_download_authority(
                entitlement=entitlement,
                reservation=reservation,
                allocations=allocations,
                funding_rows=funding_rows,
                lots=lots,
                unlock_lot=unlock_lot,
                order_id=order.id,
                user_id=order.user_id,
                now=current,
            )
            can_download = True
    visible_ids = (*preview_ids, *(final_ids if can_download else ()))
    if not visible_ids:
        return OrderAssetProjection(
            assets=(),
            can_download=can_download,
            entitlement_status=entitlement_status,
            access_tier=access_tier,
        )
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(visible_ids))
                .order_by(MediaAsset.role, MediaAsset.id)
            )
        ).all()
    )
    by_id = {item.id: item for item in assets}
    if set(by_id) != set(visible_ids):
        raise PrivateDownloadError("private_delivery_asset_set_incomplete")
    projection: list[OrderAssetRead] = []
    for asset_id in visible_ids:
        asset = by_id[asset_id]
        if (
            asset.owner_user_id != order.user_id
            or asset.order_id != order.id
            or asset.job_id != order.generation_job_id
            or _value(asset.role) not in _VISIBLE_ROLES
            or _value(asset.status) != MediaAssetStatus.ACTIVE.value
            or asset.read_revoked_at is not None
            or asset.expires_at <= current
        ):
            raise PrivateDownloadError("private_delivery_asset_invalid")
        projection.append(
            OrderAssetRead(
                id=asset.id,
                role=_value(asset.role),
                status=MediaAssetStatus.ACTIVE.value,
                width=asset.width,
                height=asset.height,
                download_path=f"/api/v1/orders/{order.id}/assets/{asset.id}/download",
            )
        )
    return OrderAssetProjection(
        assets=tuple(projection),
        can_download=can_download,
        entitlement_status=entitlement_status,
        access_tier=access_tier,
    )


async def _authorize_asset(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    asset_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> tuple[Order, MediaAsset]:
    order = await _lock_owned_order(db, order_id=order_id, user_id=user_id)
    if _value(order.status) != OrderStatus.READY.value:
        raise PrivateDownloadError("private_download_order_not_ready", status_code=409)
    await _require_delivery_order_authority(db, order)
    asset = await db.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if (
        asset is None
        or asset.owner_user_id != user_id
        or asset.order_id != order.id
        or asset.job_id != order.generation_job_id
        or _value(asset.role) not in _VISIBLE_ROLES
        or _value(asset.status) != MediaAssetStatus.ACTIVE.value
        or asset.read_revoked_at is not None
        or asset.expires_at <= now
    ):
        raise PrivateDownloadError("private_download_asset_not_found", status_code=404)
    role = _value(asset.role)
    if role == MediaAssetRole.PREVIEW_WATERMARKED.value:
        if asset.id not in _asset_ids(order.preview_asset_ids):
            raise PrivateDownloadError("private_download_asset_not_found", status_code=404)
    else:
        if asset.id not in _asset_ids(order.final_asset_ids):
            raise PrivateDownloadError("private_download_asset_not_found", status_code=404)
        reservation, allocations, lots, entitlement, funding_rows, unlock_lot = await _lock_funding_graph(
            db, order=order, require_entitlement=True
        )
        assert entitlement is not None
        validate_entitlement_download_authority(
            entitlement=entitlement,
            reservation=reservation,
            allocations=allocations,
            funding_rows=funding_rows,
            lots=lots,
            unlock_lot=unlock_lot,
            order_id=order.id,
            user_id=user_id,
            now=now,
        )
    return order, asset


async def resolve_private_download(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    asset_id: uuid.UUID,
    user_id: uuid.UUID,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> PrivateDownloadResult:
    current = _now(now)
    order, asset = await _authorize_asset(
        db, order_id=order_id, asset_id=asset_id, user_id=user_id, now=current
    )
    object_key = asset.object_key
    expected = (int(asset.byte_size), asset.sha256, asset.mime_type, _value(asset.role))
    await db.commit()
    try:
        content = await asyncio.to_thread(object_store.read_private, object_key)
    except FileNotFoundError as exc:
        raise PrivateDownloadError("private_download_object_missing", status_code=410) from exc
    order, asset = await _authorize_asset(
        db, order_id=order_id, asset_id=asset_id, user_id=user_id, now=_now(now)
    )
    if (
        expected
        != (int(asset.byte_size), asset.sha256, asset.mime_type, _value(asset.role))
        or len(content) != expected[0]
        or hashlib.sha256(content).hexdigest() != expected[1]
    ):
        raise PrivateDownloadError("private_download_object_integrity_failed", status_code=410)
    audit_id = uuid.uuid4()
    db.add(
        OutboxEvent(
            id=audit_id,
            aggregate_type="order_download_audit",
            aggregate_id=order.id,
            event_type="ORDER_ASSET_DOWNLOADED",
            dedupe_key=f"order-download:{audit_id}",
            payload_version="order-download.v1",
            payload_json={
                "order_id": str(order.id),
                "asset_id": str(asset.id),
                "user_id": str(user_id),
                "role": expected[3],
            },
            status=OutboxEventStatus.DISPATCHED,
            attempt_count=0,
            next_attempt_at=current,
            fencing_token=0,
            dispatched_at=current,
        )
    )
    await db.flush()
    filename = f"vowpic-{expected[3].replace('_', '-')}-{asset.id}.jpg"
    return PrivateDownloadResult(
        asset_id=asset.id,
        content=bytes(content),
        mime_type=asset.mime_type,
        filename=filename,
    )


async def read_order_funding(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OrderFundingRead:
    order = await _lock_owned_order(db, order_id=order_id, user_id=user_id)
    reservation, allocations, lots, entitlement, funding_rows, unlock_lot = await _lock_funding_graph(
        db, order=order, require_entitlement=False
    )
    facts = validate_captured_entitlement_funding(
        reservation=reservation, allocations=allocations, lots=lots
    )
    if entitlement is not None:
        validate_entitlement_download_authority(
            entitlement=entitlement,
            reservation=reservation,
            allocations=allocations,
            funding_rows=funding_rows,
            lots=lots,
            unlock_lot=unlock_lot,
            order_id=order.id,
            user_id=user_id,
            now=_now(None),
        )
    return OrderFundingRead(
        reservation_id=reservation.id,
        reservation_status=_value(reservation.status),
        amount=int(reservation.amount),
        allocations=tuple(
            OrderFundingAllocationRead(
                amount=item.amount,
                root_transaction_id=item.root_transaction_id,
                root_kind=item.root_kind,
            )
            for item in facts
        ),
        entitlement_status=_value(entitlement.status) if entitlement else None,
        unlock_root_transaction_id=(entitlement.unlock_root_transaction_id if entitlement else None),
        unlock_root_kind=(_value(unlock_lot.source_type) if unlock_lot else None),
    )


async def unlock_trial_order(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    root_transaction_id: uuid.UUID,
    idempotency_key: str,
    now: datetime | None = None,
) -> TrialUnlockRead:
    current = _now(now)
    request_hash = canonical_request_hash(
        {"order_id": str(order_id), "root_transaction_id": str(root_transaction_id)}
    )
    attempt = await begin_idempotent_request(
        db,
        user_id=user_id,
        endpoint="orders.unlock_trial",
        key=idempotency_key,
        request_hash=request_hash,
        now=current,
    )
    if attempt.replayed:
        if attempt.response_status == 200 and attempt.response_json is not None:
            return TrialUnlockRead.model_validate(attempt.response_json, strict=False)
        raise IdempotencyConflict("trial_unlock_idempotency_in_progress")
    order = await _lock_owned_order(db, order_id=order_id, user_id=user_id)
    if _value(order.status) != OrderStatus.READY.value or not is_trial_funding_snapshot(
        order.funding_policy_snapshot
    ):
        raise PrivateDownloadError("trial_unlock_order_invalid")
    await _require_delivery_order_authority(db, order)
    final_ids = _asset_ids(order.final_asset_ids)
    preview_ids = _asset_ids(order.preview_asset_ids)
    if len(final_ids) != 7 or len(preview_ids) != 1:
        raise PrivateDownloadError("trial_unlock_delivery_set_incomplete")
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_((*final_ids, *preview_ids)))
                .with_for_update()
            )
        ).all()
    )
    if len(assets) != 8 or any(
        asset.owner_user_id != user_id
        or asset.order_id != order.id
        or _value(asset.status) != MediaAssetStatus.ACTIVE.value
        or asset.read_revoked_at is not None
        for asset in assets
    ):
        raise PrivateDownloadError("trial_unlock_delivery_set_invalid")
    reservation, allocations, lots, existing, funding_rows, existing_unlock_lot = await _lock_funding_graph(
        db, order=order, require_entitlement=False
    )
    facts = validate_captured_entitlement_funding(
        reservation=reservation, allocations=allocations, lots=lots
    )
    if any(item.root_kind != GrantLotSourceType.WELCOME.value for item in facts):
        raise PrivateDownloadError("trial_unlock_original_funding_invalid")
    unlock_lot = await db.scalar(
        select(CreditGrantLot)
        .where(
            CreditGrantLot.user_id == user_id,
            CreditGrantLot.root_transaction_id == root_transaction_id,
        )
        .with_for_update()
    )
    if unlock_lot is None:
        raise PrivateDownloadError("trial_unlock_grant_not_found", status_code=404)
    validate_trial_unlock_grant(unlock_lot, user_id=user_id, now=current)
    if existing is not None:
        if (
            existing.unlock_grant_lot_id != unlock_lot.id
            or existing.unlock_root_transaction_id != root_transaction_id
            or _value(existing.status) != EntitlementStatus.ACTIVE.value
            or existing.access_tier != TRIAL_UNLOCKED_ACCESS_TIER
        ):
            raise PrivateDownloadError("trial_unlock_entitlement_conflict")
        validate_entitlement_download_authority(
            entitlement=existing,
            reservation=reservation,
            allocations=allocations,
            funding_rows=funding_rows,
            lots=lots,
            unlock_lot=existing_unlock_lot,
            order_id=order.id,
            user_id=user_id,
            now=current,
        )
        entitlement = existing
    else:
        already_used = await db.scalar(
            select(OrderEntitlement.id).where(
                OrderEntitlement.unlock_grant_lot_id == unlock_lot.id
            )
        )
        if already_used is not None:
            raise PrivateDownloadError("trial_unlock_grant_already_used")
        job = await db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == order.generation_job_id)
            .with_for_update()
        )
        if (
            job is None
            or _value(job.status) != GenerationJobStatus.FINISHED.value
            or job.finished_at is None
        ):
            raise PrivateDownloadError("trial_unlock_ready_time_missing")
        expires_at = retention_deadline_for_tier(
            original_ready_at=job.finished_at,
            retention_tier=unlock_lot.retention_tier,
            existing_expires_at=order.expires_at,
        )
        entitlement = OrderEntitlement(
            id=uuid.uuid5(uuid.UUID("b9486043-e5ef-456d-b0a0-f46fc268bde7"), str(order.id)),
            order_id=order.id,
            user_id=user_id,
            reservation_id=reservation.id,
            unlock_grant_lot_id=unlock_lot.id,
            unlock_root_transaction_id=root_transaction_id,
            status=EntitlementStatus.ACTIVE,
            access_tier=TRIAL_UNLOCKED_ACCESS_TIER,
            retention_tier=unlock_lot.retention_tier,
            expires_at=expires_at,
        )
        db.add(entitlement)
        await db.flush()
        for fact in facts:
            db.add(
                OrderEntitlementFunding(
                    id=uuid.uuid5(entitlement.id, str(fact.allocation_id)),
                    entitlement_id=entitlement.id,
                    reservation_allocation_id=fact.allocation_id,
                    grant_lot_id=fact.grant_lot_id,
                    amount=fact.amount,
                )
            )
        order.expires_at = expires_at
        for asset in assets:
            asset.expires_at = max(asset.expires_at, expires_at)
        await db.flush()
    response = TrialUnlockRead(
        entitlement_id=entitlement.id,
        order_id=order.id,
        status=EntitlementStatus.ACTIVE.value,
        access_tier=TRIAL_UNLOCKED_ACCESS_TIER,
        expires_at=entitlement.expires_at,
    )
    await complete_idempotent_request(
        db,
        record_id=attempt.record_id,
        response_status=200,
        response_json=response.model_dump(mode="json"),
    )
    await db.flush()
    return response
