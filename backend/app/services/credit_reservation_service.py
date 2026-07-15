"""Deterministic grant-lot reservation, capture, release, and refund primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_reservation import (
    CreditReservation,
    CreditReservationAllocation,
    ReservationStatus,
)
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob
from app.models.user_credit import UserCredit
from app.services.idempotency_service import IdempotencyConflict, lock_idempotency_scope


RESERVATION_TTL = timedelta(minutes=30)
WELCOME_TRIAL_COST = 2
FUNDING_POLICY_VERSION = "order-funding.v1"

_PAID_LOT_TYPES = frozenset(
    {
        GrantLotSourceType.PURCHASE,
        GrantLotSourceType.SUBSCRIPTION,
        GrantLotSourceType.ADMIN,
        GrantLotSourceType.REFUND,
        GrantLotSourceType.LEGACY_POOL,
    }
)
_RETENTION_DAYS = {
    "welcome_30d": 30,
    "free_30d": 30,
    "paid_90d": 90,
    "subscription_180d": 180,
    "studio_365d": 365,
}


class CreditReservationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class FundingPolicyViolation(CreditReservationError):
    pass


class InsufficientCredits(CreditReservationError):
    def __init__(self, required: int):
        self.required = int(required)
        super().__init__("insufficient_credits")


class InvalidReservationTransition(CreditReservationError):
    pass


class CreditInvariantViolation(CreditReservationError):
    pass


@dataclass(frozen=True, slots=True)
class CreditFundingLock:
    user_id: uuid.UUID
    amount: int
    policy_hash: str
    credit: UserCredit
    lots: tuple[CreditGrantLot, ...]
    reserved_by_lot: dict[uuid.UUID, int]


def validate_capture_attempt_provenance(
    reservation: CreditReservation,
    attempt: GenerationAttempt | None,
    job: GenerationJob | None,
) -> None:
    """Require the exact submitted INITIAL attempt for this reservation's order."""

    if attempt is None or job is None:
        raise CreditInvariantViolation("capture_attempt_not_found")
    if GenerationAttemptKind(attempt.kind) is not GenerationAttemptKind.INITIAL:
        raise CreditInvariantViolation("capture_attempt_not_initial")
    if attempt.job_id != job.id or job.order_id != reservation.order_id:
        raise CreditInvariantViolation("capture_attempt_job_mismatch")
    if str(attempt.client_request_id) != str(job.submission_correlation_id):
        raise CreditInvariantViolation("capture_attempt_correlation_mismatch")
    if GenerationAttemptStatus(attempt.status) not in {
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.FINISHED,
    }:
        raise CreditInvariantViolation("capture_attempt_not_submitted")


async def _lock_capture_attempt_and_job(
    db: AsyncSession,
    provider_attempt_id: uuid.UUID,
) -> tuple[GenerationAttempt, GenerationJob]:
    """Lock immutable capture lineage in the global job -> attempt order."""

    hint = await db.scalar(
        select(GenerationAttempt).where(GenerationAttempt.id == provider_attempt_id)
    )
    if hint is None:
        raise CreditInvariantViolation("capture_attempt_not_found")
    job = await db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == hint.job_id)
        .with_for_update()
    )
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == provider_attempt_id)
        .with_for_update()
    )
    if attempt is None or job is None or attempt.job_id != job.id:
        raise CreditInvariantViolation("capture_attempt_not_found")
    return attempt, job


class OrderFundingPolicySnapshot(BaseModel):
    """Immutable server-derived facts that govern which grant class may fund an order."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_version: str = FUNDING_POLICY_VERSION
    generation_mode: str = Field(min_length=1, max_length=64)
    subject_count: int = Field(ge=1, le=2)
    is_trial: bool
    identity_claim_id: uuid.UUID | None
    attempts_in_rolling_24h: int = Field(ge=0)
    ready_trial_exists: bool
    allowed_lot_class: Literal["WELCOME_ONLY", "PAID_ONLY"]
    scene_tier: str = Field(min_length=1, max_length=32)
    director_mode: bool

    @model_validator(mode="after")
    def _coherent_lot_class(self) -> "OrderFundingPolicySnapshot":
        if self.is_trial != (self.allowed_lot_class == "WELCOME_ONLY"):
            raise ValueError("trial_lot_class_mismatch")
        return self

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    def allowed_source_types(self) -> frozenset[GrantLotSourceType]:
        if self.allowed_lot_class == "WELCOME_ONLY":
            return frozenset({GrantLotSourceType.WELCOME})
        return _PAID_LOT_TYPES

    def validate_for_amount(self, amount: int) -> None:
        normalized_mode = self.generation_mode.strip().lower()
        normalized_scene = self.scene_tier.strip().lower()
        if self.policy_version != FUNDING_POLICY_VERSION:
            raise FundingPolicyViolation("funding_policy_version_unsupported")
        if not self.is_trial:
            if self.allowed_lot_class != "PAID_ONLY":
                raise FundingPolicyViolation("paid_funding_class_invalid")
            return
        if int(amount) != WELCOME_TRIAL_COST:
            raise FundingPolicyViolation("trial_amount_invalid")
        if self.identity_claim_id is None:
            raise FundingPolicyViolation("trial_identity_claim_required")
        if normalized_mode != "single" or self.subject_count != 1:
            raise FundingPolicyViolation("trial_single_subject_required")
        if normalized_scene != "base" or self.director_mode:
            raise FundingPolicyViolation("trial_base_mode_required")
        if self.attempts_in_rolling_24h >= 3:
            raise FundingPolicyViolation("trial_attempt_limit_reached")
        if self.ready_trial_exists:
            raise FundingPolicyViolation("trial_already_delivered")


@dataclass(frozen=True, slots=True)
class FefoAllocation:
    grant_lot_id: uuid.UUID
    amount: int


@dataclass(frozen=True, slots=True)
class CreditSettlement:
    reservation: CreditReservation
    transaction: CreditTransaction | None
    replayed: bool


def _source_type(value: GrantLotSourceType | str) -> GrantLotSourceType:
    return value if isinstance(value, GrantLotSourceType) else GrantLotSourceType(str(value))


def _status(value: ReservationStatus | str) -> str:
    return value.value if isinstance(value, ReservationStatus) else str(value)


def _lot_spendable(lot: CreditGrantLot) -> int:
    value = getattr(lot, "spendable_amount")
    return max(0, int(value() if callable(value) else value))


def _lot_sort_key(lot: CreditGrantLot) -> tuple:
    expires_at = getattr(lot, "expires_at", None)
    created_at = getattr(lot, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc)
    fallback_expiry = datetime.max.replace(tzinfo=timezone.utc)
    return (
        expires_at is None,
        expires_at or fallback_expiry,
        created_at,
        str(lot.id),
    )


def build_fefo_allocations(
    lots: list[CreditGrantLot],
    *,
    amount: int,
    reserved_by_lot: dict[uuid.UUID, int],
    now: datetime,
) -> list[FefoAllocation]:
    required = int(amount)
    if required <= 0:
        raise ValueError("reservation_amount_must_be_positive")
    remaining = required
    allocations: list[FefoAllocation] = []
    for lot in sorted(lots, key=_lot_sort_key):
        if lot.expires_at is not None and lot.expires_at <= now:
            continue
        available = max(
            0,
            _lot_spendable(lot) - int(reserved_by_lot.get(lot.id, 0)),
        )
        if available <= 0:
            continue
        allocated = min(available, remaining)
        allocations.append(FefoAllocation(grant_lot_id=lot.id, amount=allocated))
        remaining -= allocated
        if remaining == 0:
            break
    if remaining:
        raise InsufficientCredits(required)
    return allocations


def captured_retention_tier(lots: list[CreditGrantLot]) -> str:
    if not lots:
        raise CreditInvariantViolation("reservation_allocations_missing")
    tiers = [str(lot.retention_tier) for lot in lots]
    unknown = [tier for tier in tiers if tier not in _RETENTION_DAYS]
    if unknown:
        raise CreditInvariantViolation("retention_tier_unknown")
    return max(tiers, key=lambda tier: _RETENTION_DAYS[tier])


async def _locked_credit(db: AsyncSession, user_id: uuid.UUID) -> UserCredit:
    credit = await db.scalar(
        select(UserCredit)
        .where(UserCredit.user_id == user_id)
        .with_for_update()
    )
    if credit is None:
        raise InsufficientCredits(1)
    return credit


async def _reserved_amounts_by_lot(
    db: AsyncSession,
    *,
    lot_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not lot_ids:
        return {}
    rows = (
        await db.execute(
            select(
                CreditReservationAllocation.grant_lot_id,
                func.sum(CreditReservationAllocation.amount),
            )
            .join(
                CreditReservation,
                CreditReservation.id == CreditReservationAllocation.reservation_id,
            )
            .where(
                CreditReservationAllocation.grant_lot_id.in_(lot_ids),
                CreditReservation.status == ReservationStatus.RESERVED.value,
            )
            .group_by(CreditReservationAllocation.grant_lot_id)
        )
    ).all()
    return {lot_id: int(total or 0) for lot_id, total in rows}


async def reserve_credits(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    amount: int,
    funding_policy: OrderFundingPolicySnapshot,
    idempotency_key: str,
    now: datetime,
    funding_lock: CreditFundingLock | None = None,
) -> CreditReservation:
    amount = int(amount)
    if now.tzinfo is None:
        raise ValueError("reservation_now_must_be_timezone_aware")
    funding_policy.validate_for_amount(amount)
    policy_hash = funding_policy.canonical_hash()
    await lock_idempotency_scope(
        db,
        user_id=user_id,
        endpoint="credit.reserve",
        key=idempotency_key,
    )
    existing = await db.scalar(
        select(CreditReservation)
        .where(
            CreditReservation.user_id == user_id,
            CreditReservation.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.order_id != order_id
            or int(existing.amount) != amount
            or existing.funding_policy_hash != policy_hash
        ):
            raise IdempotencyConflict("reservation_payload_mismatch")
        return existing

    locked = funding_lock or await lock_credit_funding(
        db,
        user_id=user_id,
        amount=amount,
        funding_policy=funding_policy,
        now=now,
    )
    if (
        locked.user_id != user_id
        or locked.amount != amount
        or locked.policy_hash != policy_hash
    ):
        raise CreditInvariantViolation("reservation_funding_lock_mismatch")
    credit = locked.credit
    lots = list(locked.lots)
    allocations = build_fefo_allocations(
        lots,
        amount=amount,
        reserved_by_lot=locked.reserved_by_lot,
        now=now,
    )
    reservation = CreditReservation(
        id=uuid.uuid4(),
        user_id=user_id,
        order_id=order_id,
        amount=amount,
        status=ReservationStatus.RESERVED,
        idempotency_key=idempotency_key,
        request_hash=policy_hash,
        funding_policy_snapshot=funding_policy.model_dump(mode="json"),
        funding_policy_hash=policy_hash,
        expires_at=now + RESERVATION_TTL,
    )
    db.add(reservation)
    for allocation in allocations:
        db.add(
            CreditReservationAllocation(
                id=uuid.uuid4(),
                reservation_id=reservation.id,
                grant_lot_id=allocation.grant_lot_id,
                amount=allocation.amount,
            )
        )
    credit.reserved_balance = int(credit.reserved_balance or 0) + amount
    await db.flush()
    return reservation


async def lock_credit_funding(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    amount: int,
    funding_policy: OrderFundingPolicySnapshot,
    now: datetime,
) -> CreditFundingLock:
    """Lock the exact credit/lots before a caller acquires its request idempotency lock."""

    amount = int(amount)
    if now.tzinfo is None:
        raise ValueError("reservation_now_must_be_timezone_aware")
    funding_policy.validate_for_amount(amount)
    credit = await _locked_credit(db, user_id)
    if credit.spendable_balance < amount:
        raise InsufficientCredits(amount)
    allowed_types = [item.value for item in funding_policy.allowed_source_types()]
    lots = list(
        (
            await db.scalars(
                select(CreditGrantLot)
                .where(
                    CreditGrantLot.user_id == user_id,
                    CreditGrantLot.source_type.in_(allowed_types),
                    or_(CreditGrantLot.expires_at.is_(None), CreditGrantLot.expires_at > now),
                )
                .order_by(
                    CreditGrantLot.expires_at.asc().nullslast(),
                    CreditGrantLot.created_at,
                    CreditGrantLot.id,
                )
                .with_for_update()
            )
        ).all()
    )
    lots = [
        lot
        for lot in lots
        if _source_type(lot.source_type) in funding_policy.allowed_source_types()
    ]
    reserved_by_lot = await _reserved_amounts_by_lot(
        db,
        lot_ids=[lot.id for lot in lots],
    )
    build_fefo_allocations(
        lots,
        amount=amount,
        reserved_by_lot=reserved_by_lot,
        now=now,
    )
    return CreditFundingLock(
        user_id=user_id,
        amount=amount,
        policy_hash=funding_policy.canonical_hash(),
        credit=credit,
        lots=tuple(lots),
        reserved_by_lot=reserved_by_lot,
    )


async def _locked_reservation(
    db: AsyncSession,
    reservation_id: uuid.UUID,
) -> CreditReservation:
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise LookupError("reservation_not_found")
    return reservation


async def _locked_allocations_and_lots(
    db: AsyncSession,
    reservation: CreditReservation,
) -> tuple[list[CreditReservationAllocation], list[CreditGrantLot]]:
    allocations = list(
        (
            await db.scalars(
                select(CreditReservationAllocation)
                .where(CreditReservationAllocation.reservation_id == reservation.id)
                .order_by(CreditReservationAllocation.created_at, CreditReservationAllocation.id)
                .with_for_update()
            )
        ).all()
    )
    if sum(int(item.amount) for item in allocations) != int(reservation.amount):
        raise CreditInvariantViolation("reservation_allocation_sum_mismatch")
    lot_ids = [item.grant_lot_id for item in allocations]
    lots = list(
        (
            await db.scalars(
                select(CreditGrantLot)
                .where(CreditGrantLot.id.in_(lot_ids))
                .order_by(CreditGrantLot.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {lot.id: lot for lot in lots}
    if set(by_id) != set(lot_ids):
        raise CreditInvariantViolation("reservation_grant_lot_missing")
    return allocations, [by_id[item.grant_lot_id] for item in allocations]


async def require_initial_submission_reservation(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> CreditReservation:
    """Prove held funding is intact before any INITIAL Provider side effect."""
    reservation = await _locked_reservation(db, reservation_id)
    if reservation.order_id != order_id or reservation.user_id != user_id:
        raise CreditInvariantViolation("submission_reservation_owner_mismatch")
    if _status(reservation.status) != ReservationStatus.RESERVED.value:
        raise CreditInvariantViolation("submission_reservation_not_reserved")
    if reservation.expires_at <= now:
        raise CreditInvariantViolation("submission_reservation_expired")
    if reservation.provider_attempt_id is not None:
        raise CreditInvariantViolation("submission_reservation_attempt_already_set")
    credit = await _locked_credit(db, user_id)
    if int(credit.reserved_balance or 0) < int(reservation.amount):
        raise CreditInvariantViolation("submission_reserved_balance_underfunded")
    allocations, lots = await _locked_allocations_and_lots(db, reservation)
    for allocation, lot in zip(allocations, lots, strict=True):
        if _lot_spendable(lot) < int(allocation.amount):
            raise CreditInvariantViolation("submission_reservation_lot_underfunded")
    return reservation


async def _transaction_by_request(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_type: CreditTransactionType,
    request_id: str,
) -> CreditTransaction | None:
    return await db.scalar(
        select(CreditTransaction).where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.transaction_type == transaction_type,
            CreditTransaction.request_id == request_id,
        )
    )


async def capture_reservation(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    provider_attempt_id: uuid.UUID,
    idempotency_key: str,
    now: datetime | None = None,
) -> CreditReservation:
    current = now or datetime.now(timezone.utc)
    attempt, job = await _lock_capture_attempt_and_job(db, provider_attempt_id)
    reservation = await _locked_reservation(db, reservation_id)
    validate_capture_attempt_provenance(reservation, attempt, job)
    if _status(reservation.status) == ReservationStatus.CAPTURED.value:
        if reservation.provider_attempt_id != provider_attempt_id:
            raise IdempotencyConflict("capture_attempt_mismatch")
        return reservation
    if _status(reservation.status) != ReservationStatus.RESERVED.value:
        raise InvalidReservationTransition("reservation_not_capturable")
    credit = await _locked_credit(db, reservation.user_id)
    if int(credit.reserved_balance or 0) < int(reservation.amount):
        raise CreditInvariantViolation("reserved_balance_underflow")
    allocations, lots = await _locked_allocations_and_lots(db, reservation)
    for allocation, lot in zip(allocations, lots, strict=True):
        if _lot_spendable(lot) < int(allocation.amount):
            raise CreditInvariantViolation("reserved_grant_no_longer_spendable")

    existing_transaction = await _transaction_by_request(
        db,
        user_id=reservation.user_id,
        transaction_type=CreditTransactionType.GENERATION_DEBIT,
        request_id=idempotency_key,
    )
    if existing_transaction is not None:
        raise CreditInvariantViolation("capture_transaction_without_captured_reservation")
    next_balance = int(credit.balance or 0) - int(reservation.amount)
    transaction = CreditTransaction(
        id=uuid.uuid4(),
        user_id=reservation.user_id,
        transaction_type=CreditTransactionType.GENERATION_DEBIT,
        amount=-int(reservation.amount),
        balance_after=next_balance,
        source="credit_reservation",
        source_id=str(reservation.id),
        request_id=idempotency_key,
        provider_attempt_id=provider_attempt_id,
    )
    db.add(transaction)
    for allocation, lot in zip(allocations, lots, strict=True):
        lot.consumed_amount = int(lot.consumed_amount or 0) + int(allocation.amount)
    credit.reserved_balance = int(credit.reserved_balance or 0) - int(reservation.amount)
    credit.balance = next_balance
    reservation.status = ReservationStatus.CAPTURED
    reservation.provider_attempt_id = provider_attempt_id
    reservation.captured_transaction_id = transaction.id
    reservation.captured_at = current
    reservation.captured_retention_tier = captured_retention_tier(lots)
    reservation.updated_at = current
    await db.flush()
    return reservation


async def release_reservation(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    idempotency_key: str,
    pre_submission_confirmed: bool,
    expired: bool = False,
    now: datetime | None = None,
) -> CreditSettlement:
    current = now or datetime.now(timezone.utc)
    reservation = await _locked_reservation(db, reservation_id)
    target = ReservationStatus.EXPIRED if expired else ReservationStatus.RELEASED
    if _status(reservation.status) == target.value:
        return CreditSettlement(reservation, None, True)
    if _status(reservation.status) != ReservationStatus.RESERVED.value:
        raise InvalidReservationTransition("reservation_not_releasable")
    if not pre_submission_confirmed:
        raise InvalidReservationTransition("provider_submission_state_unconfirmed")
    if expired and current < reservation.expires_at:
        raise InvalidReservationTransition("reservation_not_expired")
    await lock_idempotency_scope(
        db,
        user_id=reservation.user_id,
        endpoint=f"credit.{target.value.lower()}",
        key=idempotency_key,
    )
    credit = await _locked_credit(db, reservation.user_id)
    if int(credit.reserved_balance or 0) < int(reservation.amount):
        raise CreditInvariantViolation("reserved_balance_underflow")
    credit.reserved_balance = int(credit.reserved_balance or 0) - int(reservation.amount)
    reservation.status = target
    reservation.released_at = current
    reservation.updated_at = current
    await db.flush()
    return CreditSettlement(reservation, None, False)


async def refund_captured_reservation(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    idempotency_key: str,
    reason_code: str,
    now: datetime | None = None,
) -> CreditSettlement:
    current = now or datetime.now(timezone.utc)
    reservation = await _locked_reservation(db, reservation_id)
    if _status(reservation.status) != ReservationStatus.CAPTURED.value:
        raise InvalidReservationTransition("reservation_not_refundable")
    existing = await _transaction_by_request(
        db,
        user_id=reservation.user_id,
        transaction_type=CreditTransactionType.GENERATION_REFUND,
        request_id=idempotency_key,
    )
    if existing is not None:
        if existing.source_id != str(reservation.id):
            raise IdempotencyConflict("refund_reservation_mismatch")
        return CreditSettlement(reservation, existing, True)
    credit = await _locked_credit(db, reservation.user_id)
    amount = int(reservation.amount)
    next_balance = int(credit.balance or 0) + amount
    transaction_id = uuid.uuid4()
    transaction = CreditTransaction(
        id=transaction_id,
        user_id=reservation.user_id,
        transaction_type=CreditTransactionType.GENERATION_REFUND,
        amount=amount,
        balance_after=next_balance,
        source="credit_reservation",
        source_id=str(reservation.id),
        description=str(reason_code)[:256],
        request_id=idempotency_key,
        root_transaction_id=transaction_id,
        reversal_of_transaction_id=reservation.captured_transaction_id,
        provider_attempt_id=reservation.provider_attempt_id,
    )
    debt_offset = min(amount, max(0, -int(credit.balance or 0)))
    lot = CreditGrantLot(
        id=uuid.uuid4(),
        user_id=reservation.user_id,
        root_transaction_id=transaction_id,
        source_type=GrantLotSourceType.REFUND,
        source_id=str(reservation.id),
        original_amount=amount,
        debt_offset_amount=debt_offset,
        reversed_amount=0,
        frozen_amount=0,
        consumed_amount=0,
        retention_tier=reservation.captured_retention_tier or "paid_90d",
        expires_at=current + timedelta(days=90),
    )
    db.add(transaction)
    db.add(lot)
    credit.balance = next_balance
    await db.flush()
    return CreditSettlement(reservation, transaction, False)


async def release_or_refund_reservation(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    settlement: Literal["RELEASE", "GENERATION_REFUND"],
    idempotency_key: str,
    pre_submission_confirmed: bool = False,
    reason_code: str = "generation_failed",
    now: datetime | None = None,
) -> CreditSettlement:
    if settlement == "GENERATION_REFUND":
        return await refund_captured_reservation(
            db,
            reservation_id=reservation_id,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            now=now,
        )
    return await release_reservation(
        db,
        reservation_id=reservation_id,
        idempotency_key=idempotency_key,
        pre_submission_confirmed=pre_submission_confirmed,
        now=now,
    )
