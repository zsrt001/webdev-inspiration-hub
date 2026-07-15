"""Bounded compensation against immutable root credit grants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_reservation import CreditReservation, CreditReservationAllocation, ReservationStatus
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.order_entitlement_funding import OrderEntitlementFunding
from app.models.user_credit import UserCredit
from app.services.idempotency_service import IdempotencyConflict, lock_idempotency_scope


class CreditReversalError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CreditReversalResult:
    transaction: CreditTransaction
    debt: int
    replayed: bool


def apply_root_reversal_counters(
    lot: CreditGrantLot,
    credit: UserCredit,
    *,
    amount: int,
) -> int:
    reversal = int(amount)
    if reversal <= 0:
        raise CreditReversalError("reversal_amount_invalid")
    if int(lot.reversed_amount or 0) + reversal > int(lot.original_amount):
        raise CreditReversalError("root_reversal_cap_exceeded")
    lot.reversed_amount = int(lot.reversed_amount or 0) + reversal
    lot.frozen_amount = max(0, int(lot.frozen_amount or 0) - reversal)
    credit.balance = int(credit.balance or 0) - reversal
    return max(0, -int(credit.balance or 0))


async def reverse_root_grant(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    root_transaction_id: uuid.UUID,
    amount: int,
    request_id: str,
    reason_code: str,
    transaction_type: CreditTransactionType = CreditTransactionType.ROOT_REVERSAL,
    now: datetime | None = None,
) -> CreditReversalResult:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("reversal_now_must_be_timezone_aware")
    clean_request_id = str(request_id or "").strip()
    clean_reason = str(reason_code or "").strip()
    if not clean_request_id or len(clean_request_id) > 128:
        raise CreditReversalError("reversal_request_id_invalid")
    if not clean_reason or len(clean_reason) > 256:
        raise CreditReversalError("reversal_reason_required")
    allowed_types = {
        CreditTransactionType.ROOT_REVERSAL,
        CreditTransactionType.PURCHASE_REVERSAL,
        CreditTransactionType.DISPUTE_REVERSAL,
        CreditTransactionType.SUBSCRIPTION_REVERSAL,
    }
    if transaction_type not in allowed_types:
        raise CreditReversalError("reversal_transaction_type_invalid")
    await lock_idempotency_scope(
        db,
        user_id=user_id,
        endpoint=f"credit.root_reversal.{transaction_type.value.lower()}",
        key=clean_request_id,
    )
    existing = await db.scalar(
        select(CreditTransaction).where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.transaction_type == transaction_type,
            CreditTransaction.request_id == clean_request_id,
        )
    )
    if existing is not None:
        if (
            existing.root_transaction_id != root_transaction_id
            or int(existing.amount) != -int(amount)
        ):
            raise IdempotencyConflict("reversal_payload_mismatch")
        return CreditReversalResult(
            transaction=existing,
            debt=max(0, -int(existing.balance_after)),
            replayed=True,
        )

    lot = await db.scalar(
        select(CreditGrantLot)
        .where(
            CreditGrantLot.user_id == user_id,
            CreditGrantLot.root_transaction_id == root_transaction_id,
        )
        .with_for_update()
    )
    if lot is None:
        raise CreditReversalError("root_grant_not_found")
    active_reservation_count = int(
        await db.scalar(
            select(func.count(CreditReservationAllocation.id))
            .join(
                CreditReservation,
                CreditReservation.id == CreditReservationAllocation.reservation_id,
            )
            .where(
                CreditReservationAllocation.grant_lot_id == lot.id,
                CreditReservation.status == ReservationStatus.RESERVED.value,
            )
        )
        or 0
    )
    if active_reservation_count:
        raise CreditReversalError("root_has_active_reservation")
    credit = await db.scalar(
        select(UserCredit)
        .where(UserCredit.user_id == user_id)
        .with_for_update()
    )
    if credit is None:
        raise CreditReversalError("credit_account_missing")
    debt = apply_root_reversal_counters(lot, credit, amount=int(amount))
    transaction = CreditTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        transaction_type=transaction_type,
        amount=-int(amount),
        balance_after=int(credit.balance or 0),
        source="root_grant_reversal",
        source_id=str(root_transaction_id),
        description=clean_reason,
        root_transaction_id=root_transaction_id,
        reversal_of_transaction_id=root_transaction_id,
        request_id=clean_request_id,
    )
    db.add(transaction)
    funded_entitlement_ids = set(
        (
            await db.scalars(
                select(OrderEntitlementFunding.entitlement_id).where(
                    OrderEntitlementFunding.grant_lot_id == lot.id
                )
            )
        ).all()
    )
    unlock_entitlement_ids = set(
        (
            await db.scalars(
                select(OrderEntitlement.id).where(
                    OrderEntitlement.unlock_grant_lot_id == lot.id
                )
            )
        ).all()
    )
    entitlement_ids = funded_entitlement_ids | unlock_entitlement_ids
    entitlements = []
    if entitlement_ids:
        entitlements = list(
            (
                await db.scalars(
                    select(OrderEntitlement)
                    .where(
                        OrderEntitlement.id.in_(entitlement_ids),
                        OrderEntitlement.status == EntitlementStatus.ACTIVE.value,
                    )
                    .order_by(OrderEntitlement.id)
                    .with_for_update()
                )
            ).all()
        )
    for entitlement in entitlements:
        entitlement.status = EntitlementStatus.REVOKED
        entitlement.revoked_at = current
        entitlement.revoke_reason = clean_reason[:64]
    await db.flush()
    return CreditReversalResult(transaction=transaction, debt=debt, replayed=False)
