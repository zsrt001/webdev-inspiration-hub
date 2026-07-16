"""Bounded REPAIR attempts with immutable verdict and capture lineage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.order import Order, OrderStatus
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.qa_verdict import QaDecision, QaVerdict
from app.services.credit_reservation_service import release_or_refund_reservation
from app.services.generation_job_service import validate_job_transition
from app.services.job_lease_service import require_current_generation_fence


GENERATION_ATTEMPT_PAYLOAD_VERSION = "generation-attempt.v1"
GENERATION_ATTEMPT_AGGREGATE_TYPE = "generation_attempt"
GENERATION_ATTEMPT_CREATED_EVENT = "GENERATION_ATTEMPT_CREATED"
MAX_CANDIDATE_REPAIRS = 2
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class RepairInvariantError(RuntimeError):
    """A repair would violate bounded lineage or financial provenance."""


class QaDispositionKind(StrEnum):
    READY_FOR_DELIVERY = "READY_FOR_DELIVERY"
    CREATE_REPAIR = "CREATE_REPAIR"
    FAIL_AND_SETTLE = "FAIL_AND_SETTLE"


@dataclass(frozen=True, slots=True)
class QaDisposition:
    kind: QaDispositionKind
    attempt_id: uuid.UUID | None = None
    replayed: bool = False


def _repair_reasons(verdict: QaVerdict) -> tuple[str, ...]:
    raw = verdict.reasons
    if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
        raise RepairInvariantError("generation_repair_reasons_invalid")
    reasons = tuple(str(value) for value in raw)
    if len(set(reasons)) != len(reasons) or any(
        _REASON_CODE.fullmatch(value) is None for value in reasons
    ):
        raise RepairInvariantError("generation_repair_reasons_invalid")
    return reasons


def validate_repair_capture_provenance(
    *,
    job: GenerationJob,
    reservation: CreditReservation,
    initial_attempt: GenerationAttempt,
) -> None:
    """Prove that a repair reuses the one captured INITIAL authorization."""

    try:
        reservation_status = ReservationStatus(reservation.status)
        initial_kind = GenerationAttemptKind(initial_attempt.kind)
    except (TypeError, ValueError) as exc:
        raise RepairInvariantError("generation_repair_capture_provenance_invalid") from exc
    if (
        reservation_status is not ReservationStatus.CAPTURED
        or reservation.order_id != job.order_id
        or reservation.provider_attempt_id != initial_attempt.id
        or initial_attempt.job_id != job.id
        or initial_kind is not GenerationAttemptKind.INITIAL
        or initial_attempt.submission_accounting_state != "CAPTURED"
        or reservation.captured_transaction_id is None
    ):
        raise RepairInvariantError("generation_repair_capture_provenance_invalid")


def build_repair_attempt(*, job: GenerationJob, verdict: QaVerdict) -> GenerationAttempt:
    """Build one deterministic repair row; persistence enforces replay uniqueness."""

    try:
        decision = QaDecision(verdict.decision)
    except (TypeError, ValueError) as exc:
        raise RepairInvariantError("generation_repair_verdict_invalid") from exc
    if decision is not QaDecision.REPAIR or verdict.job_id != job.id:
        raise RepairInvariantError("generation_repair_verdict_lineage_mismatch")
    if not isinstance(verdict.id, uuid.UUID) or verdict.candidate_asset_id is None:
        raise RepairInvariantError("generation_repair_verdict_lineage_mismatch")
    repair_count = int(job.repair_count or 0)
    if repair_count < 0:
        raise RepairInvariantError("generation_repair_count_invalid")
    if repair_count >= MAX_CANDIDATE_REPAIRS:
        raise RepairInvariantError("generation_repair_limit_reached")
    reasons = _repair_reasons(verdict)
    repair = GenerationAttempt.prepared(
        job=job,
        attempt_number=repair_count + 2,
        kind=GenerationAttemptKind.REPAIR,
        provider="evolink",
        client_request_id=f"repair:{verdict.id}",
        source_verdict_id=verdict.id,
    )
    repair.request_snapshot = {
        "schema": "generation-repair.v1",
        "source_verdict_id": str(verdict.id),
        "source_attempt_id": str(verdict.attempt_id),
        "candidate_asset_id": str(verdict.candidate_asset_id),
        "reason_codes": list(reasons),
    }
    return repair


def build_repair_outbox_event(
    *,
    repair: GenerationAttempt,
    now: datetime | None = None,
) -> OutboxEvent:
    """Create the one IDs-only, replay-deduplicated handoff for a repair row."""

    if (
        GenerationAttemptKind(repair.kind) is not GenerationAttemptKind.REPAIR
        or not isinstance(repair.id, uuid.UUID)
        or not isinstance(repair.source_verdict_id, uuid.UUID)
    ):
        raise RepairInvariantError("generation_repair_outbox_lineage_invalid")
    current = now or datetime.now(timezone.utc)
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=GENERATION_ATTEMPT_AGGREGATE_TYPE,
        aggregate_id=repair.id,
        event_type=GENERATION_ATTEMPT_CREATED_EVENT,
        dedupe_key=f"generation-attempt:v1:{repair.id}",
        payload_version=GENERATION_ATTEMPT_PAYLOAD_VERSION,
        payload_json={
            "attempt_id": str(repair.id),
            "payload_version": GENERATION_ATTEMPT_PAYLOAD_VERSION,
        },
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=current,
        fencing_token=0,
    )


async def _load_verdict(db: AsyncSession, verdict_id: uuid.UUID) -> QaVerdict:
    verdict = await db.scalar(select(QaVerdict).where(QaVerdict.id == verdict_id))
    if verdict is None:
        raise RepairInvariantError("generation_qa_verdict_not_found")
    return verdict


async def _load_existing_repair(
    db: AsyncSession,
    verdict_id: uuid.UUID,
) -> GenerationAttempt | None:
    return await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.source_verdict_id == verdict_id)
        .with_for_update()
    )


async def _load_order_for_update(db: AsyncSession, job: GenerationJob) -> Order:
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if order is None or order.generation_job_id != job.id:
        raise RepairInvariantError("generation_repair_order_lineage_mismatch")
    return order


async def _load_repair_capture_lineage(
    db: AsyncSession,
    job: GenerationJob,
) -> tuple[Order, CreditReservation, GenerationAttempt]:
    """Lock job lineage in job -> initial attempt -> reservation order."""

    order = await _load_order_for_update(db, job)
    if order.reservation_id is None:
        raise RepairInvariantError("generation_repair_reservation_missing")
    reservation_hint = await db.scalar(
        select(CreditReservation).where(CreditReservation.id == order.reservation_id)
    )
    if reservation_hint is None or reservation_hint.provider_attempt_id is None:
        raise RepairInvariantError("generation_repair_capture_provenance_invalid")
    initial = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == reservation_hint.provider_attempt_id)
        .with_for_update()
    )
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == order.reservation_id)
        .with_for_update()
    )
    if reservation is None or initial is None:
        raise RepairInvariantError("generation_repair_capture_provenance_invalid")
    validate_repair_capture_provenance(
        job=job,
        reservation=reservation,
        initial_attempt=initial,
    )
    return order, reservation, initial


def _release_lease(job: GenerationJob) -> None:
    job.lease_owner = None
    job.lease_claim_id = None
    job.lease_expires_at = None
    job.heartbeat_at = None


async def fail_generation_and_settle(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    reason_code: str,
    reason_detail: str | None = None,
    allow_release_reserved: bool = False,
    worker_id: str,
    lease_claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime,
) -> None:
    job = await require_current_generation_fence(
        db,
        job_id=job_id,
        worker_id=worker_id,
        claim_id=lease_claim_id,
        fencing_token=fencing_token,
        now=now,
    )
    order = await _load_order_for_update(db, job)
    if order.reservation_id is None:
        raise RepairInvariantError("generation_repair_reservation_missing")
    normalized_reason = str(reason_code or "generation_failed").strip()[:64]
    if not normalized_reason:
        normalized_reason = "generation_failed"
    if _REASON_CODE.fullmatch(normalized_reason) is None:
        normalized_reason = "generation_failed"
    normalized_detail = str(reason_detail or normalized_reason).strip()[:1000]
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == order.reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise RepairInvariantError("generation_repair_reservation_missing")
    reservation_status = ReservationStatus(reservation.status)
    if reservation_status is ReservationStatus.CAPTURED:
        settlement = "GENERATION_REFUND"
        settlement_status = "REFUNDED"
    elif reservation_status is ReservationStatus.RESERVED and allow_release_reserved:
        settlement = "RELEASE"
        settlement_status = "RELEASED"
    else:
        raise RepairInvariantError("generation_failure_settlement_state_invalid")
    await release_or_refund_reservation(
        db,
        reservation_id=order.reservation_id,
        settlement=settlement,
        idempotency_key=f"generation-failure:{job.id}",
        pre_submission_confirmed=settlement == "RELEASE",
        reason_code=normalized_reason,
        now=now,
    )
    job.status = validate_job_transition(job.status, GenerationJobStatus.FAILED)
    job.settlement_status = settlement_status
    job.delivery_status = "BLOCKED"
    job.last_error_code = normalized_reason
    job.last_error_detail = normalized_detail
    job.finished_at = now
    order.status = OrderStatus.FAILED
    order.settlement_status = settlement_status
    order.delivery_status = "BLOCKED"
    order.error_message = normalized_reason
    _release_lease(job)


async def decide_next_generation_action(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    verdict_id: uuid.UUID,
    worker_id: str,
    lease_claim_id: uuid.UUID,
    fencing_token: int,
    now: datetime | None = None,
) -> QaDisposition:
    """Apply exactly one fenced disposition for an immutable candidate verdict."""

    current = now or datetime.now(timezone.utc)
    job = await require_current_generation_fence(
        db,
        job_id=job_id,
        worker_id=worker_id,
        claim_id=lease_claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    verdict = await _load_verdict(db, verdict_id)
    if verdict.job_id != job.id or verdict.candidate_asset_id is None:
        raise RepairInvariantError("generation_qa_verdict_lineage_mismatch")
    decision = QaDecision(verdict.decision)
    if decision is QaDecision.PASS:
        job.delivery_status = "QA_PASSED"
        return QaDisposition(QaDispositionKind.READY_FOR_DELIVERY)

    existing = await _load_existing_repair(db, verdict.id)
    if existing is not None:
        if (
            existing.job_id != job.id
            or GenerationAttemptKind(existing.kind) is not GenerationAttemptKind.REPAIR
            or existing.source_verdict_id != verdict.id
        ):
            raise RepairInvariantError("generation_repair_replay_lineage_mismatch")
        order = await _load_order_for_update(db, job)
        order.status = OrderStatus.REPAIRING
        job.active_attempt_id = existing.id
        job.delivery_status = "REPAIRING"
        _release_lease(job)
        return QaDisposition(
            QaDispositionKind.CREATE_REPAIR,
            attempt_id=existing.id,
            replayed=True,
        )

    if decision is QaDecision.REPAIR and int(job.repair_count or 0) < MAX_CANDIDATE_REPAIRS:
        order, reservation, initial = await _load_repair_capture_lineage(db, job)
        validate_repair_capture_provenance(
            job=job,
            reservation=reservation,
            initial_attempt=initial,
        )
        repair = build_repair_attempt(job=job, verdict=verdict)
        db.add(repair)
        await db.flush()
        db.add(build_repair_outbox_event(repair=repair, now=current))
        job.repair_count = int(job.repair_count or 0) + 1
        job.active_attempt_id = repair.id
        job.delivery_status = "REPAIRING"
        order.status = OrderStatus.REPAIRING
        _release_lease(job)
        return QaDisposition(
            QaDispositionKind.CREATE_REPAIR,
            attempt_id=repair.id,
        )

    reasons = _repair_reasons(verdict)
    await fail_generation_and_settle(
        db,
        job_id=job.id,
        reason_code="qa_rejected",
        reason_detail=",".join(reasons),
        worker_id=worker_id,
        lease_claim_id=lease_claim_id,
        fencing_token=fencing_token,
        now=current,
    )
    return QaDisposition(QaDispositionKind.FAIL_AND_SETTLE)
