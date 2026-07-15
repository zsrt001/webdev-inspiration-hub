"""Evidence-preserving payment holds and monetary reconciliation cases."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_purchase import CreditPurchase
from app.models.payment_reconciliation_case import (
    PaymentReconciliationCase,
    ReconciliationCaseStatus,
)


class PaymentReconciliationRequired(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def classify_monetary_reversal(
    *,
    captured_minor_units: int,
    already_refunded_minor_units: int,
    event_minor_units: int,
) -> str:
    """Return FULL only for an exact remaining capture; never infer a ratio."""

    captured = int(captured_minor_units)
    already_refunded = int(already_refunded_minor_units)
    event_amount = int(event_minor_units)
    if captured <= 0 or already_refunded < 0 or event_amount <= 0:
        raise PaymentReconciliationRequired("monetary_fact_invalid")
    remaining = captured - already_refunded
    if remaining <= 0:
        raise PaymentReconciliationRequired("capture_already_reversed")
    if event_amount == remaining and already_refunded == 0:
        return "FULL"
    if event_amount > remaining:
        raise PaymentReconciliationRequired("monetary_reversal_exceeds_capture")
    raise PaymentReconciliationRequired("partial_monetary_reconciliation_required")


def freeze_unspent_purchase_lineage(lot: CreditGrantLot) -> int:
    """Freeze every currently spendable credit in a root lot."""

    amount = lot.spendable_amount
    if amount:
        lot.frozen_amount = int(lot.frozen_amount or 0) + amount
    return amount


def unfreeze_purchase_lineage(lot: CreditGrantLot) -> int:
    amount = int(lot.frozen_amount or 0)
    lot.frozen_amount = 0
    return amount


async def open_payment_reconciliation_case(
    db: AsyncSession,
    *,
    purchase: CreditPurchase | None,
    user_id: uuid.UUID | None = None,
    case_key: str,
    subject_type: str,
    subject_id: str,
    reason_code: str,
    raw_payload_sha256: str,
    now: datetime | None = None,
) -> PaymentReconciliationCase:
    clean_key = str(case_key or "").strip()
    clean_subject_type = str(subject_type or "").strip()
    clean_subject_id = str(subject_id or "").strip()
    clean_reason = str(reason_code or "").strip()
    clean_hash = str(raw_payload_sha256 or "").strip().lower()
    if not clean_key or len(clean_key) > 128:
        raise ValueError("reconciliation_case_key_invalid")
    if not clean_subject_type or len(clean_subject_type) > 64:
        raise ValueError("reconciliation_subject_type_invalid")
    if not clean_subject_id or len(clean_subject_id) > 128:
        raise ValueError("reconciliation_subject_id_invalid")
    if not clean_reason or len(clean_reason) > 64:
        raise ValueError("reconciliation_reason_invalid")
    if len(clean_hash) != 64 or any(char not in "0123456789abcdef" for char in clean_hash):
        raise ValueError("reconciliation_payload_hash_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("reconciliation_now_must_be_timezone_aware")

    existing = await db.scalar(
        select(PaymentReconciliationCase)
        .where(
            PaymentReconciliationCase.provider == "creem",
            PaymentReconciliationCase.case_key == clean_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.subject_type != clean_subject_type
            or existing.subject_id != clean_subject_id
            or existing.reason_code != clean_reason
            or existing.raw_payload_sha256 != clean_hash
        ):
            raise PaymentReconciliationRequired("reconciliation_case_hash_conflict")
        return existing

    case = PaymentReconciliationCase(
        id=uuid.uuid4(),
        user_id=purchase.user_id if purchase is not None else user_id,
        provider="creem",
        case_key=clean_key,
        subject_type=clean_subject_type,
        subject_id=clean_subject_id,
        reason_code=clean_reason,
        status=ReconciliationCaseStatus.OPEN,
        raw_payload_sha256=clean_hash,
        attempt_count=0,
        next_attempt_at=current,
    )
    db.add(case)
    await db.flush()
    return case


async def freeze_purchase_and_open_case(
    db: AsyncSession,
    *,
    purchase: CreditPurchase,
    case_key: str,
    reason_code: str,
    raw_payload_sha256: str,
) -> PaymentReconciliationCase:
    if purchase.grant_lot_id is not None:
        lot = await db.scalar(
            select(CreditGrantLot)
            .where(CreditGrantLot.id == purchase.grant_lot_id)
            .with_for_update()
        )
        if lot is None:
            raise PaymentReconciliationRequired("purchase_grant_lot_missing")
        freeze_unspent_purchase_lineage(lot)
    case = await open_payment_reconciliation_case(
        db,
        purchase=purchase,
        case_key=case_key,
        subject_type="credit_purchase",
        subject_id=str(purchase.id),
        reason_code=reason_code,
        raw_payload_sha256=raw_payload_sha256,
    )
    await db.flush()
    return case
