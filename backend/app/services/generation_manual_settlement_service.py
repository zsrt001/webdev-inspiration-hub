"""Audited operator closure for ambiguous EvoLink submissions without a task ID."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_access_grant import AssetAccessGrant
from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.order import Order, OrderStatus
from app.services.credit_reservation_service import (
    capture_reservation,
    release_or_refund_reservation,
)
from app.services.generation_job_service import (
    validate_attempt_transition,
    validate_job_transition,
)
from app.services.storage import StorageService


ManualResolutionAction = Literal[
    "BIND_PROVIDER_TASK",
    "CONFIRMED_NOT_ACCEPTED_RETRY",
    "FAIL_AND_SETTLE",
]
_EVIDENCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EVIDENCE_SOURCE = re.compile(r"^(EVOLINK_API|EVOLINK_DASHBOARD|EVOLINK_SUPPORT)$")
_EVIDENCE_SCHEMA = "vowpic.generation-manual-evidence.v1"
_EVIDENCE_MAX_AGE = timedelta(hours=24)


class GenerationManualSettlementError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GenerationManualCase:
    job_id: uuid.UUID
    order_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_kind: str
    submit_started_at: datetime | None
    reason_code: str
    reason_detail: str | None


@dataclass(frozen=True, slots=True)
class GenerationManualResolution:
    job_id: uuid.UUID
    order_id: uuid.UUID
    attempt_id: uuid.UUID
    action: str
    job_status: str
    attempt_status: str
    order_status: str
    settlement_status: str
    next_action: str


@dataclass(frozen=True, slots=True)
class GenerationManualEvidence:
    object_key: str
    sha256: str
    job_id: uuid.UUID
    attempt_id: uuid.UUID
    action: str


def _status_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _canonical_evidence(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _evidence_object_key(attempt_id: uuid.UUID, sha256: str) -> str:
    if _EVIDENCE_SHA256.fullmatch(sha256) is None:
        raise GenerationManualSettlementError("manual_resolution_evidence_invalid")
    return f"operations/generation-manual-evidence/{attempt_id}/{sha256}.json"


def _aware_timestamp(value: object, *, code: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GenerationManualSettlementError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GenerationManualSettlementError(code)
    return parsed.astimezone(timezone.utc)


def _provider_task_unique_conflict(exc: IntegrityError) -> bool:
    current: BaseException | None = exc
    for _ in range(5):
        if current is None:
            break
        diagnostic = getattr(current, "diag", None)
        if (
            getattr(diagnostic, "constraint_name", None)
            == "uq_generation_attempt_provider_job"
        ):
            return True
        if "uq_generation_attempt_provider_job" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def _validate_request(
    *,
    action: str,
    provider_evidence_object_key: str,
    provider_evidence_sha256: str,
    operator_reason: str,
    provider_task_id: str | None,
    provider_accepted: bool | None,
) -> tuple[ManualResolutionAction, str, str, str, str | None, bool | None]:
    normalized_action = str(action or "").strip().upper()
    if normalized_action not in {
        "BIND_PROVIDER_TASK",
        "CONFIRMED_NOT_ACCEPTED_RETRY",
        "FAIL_AND_SETTLE",
    }:
        raise GenerationManualSettlementError("manual_resolution_action_invalid")
    evidence = str(provider_evidence_sha256 or "").strip().lower()
    if _EVIDENCE_SHA256.fullmatch(evidence) is None:
        raise GenerationManualSettlementError("manual_resolution_evidence_invalid")
    object_key = str(provider_evidence_object_key or "").strip()
    if (
        not object_key.startswith("operations/generation-manual-evidence/")
        or not object_key.endswith(f"/{evidence}.json")
    ):
        raise GenerationManualSettlementError("manual_resolution_evidence_reference_invalid")
    reason = str(operator_reason or "").strip()
    if not 8 <= len(reason) <= 500:
        raise GenerationManualSettlementError("manual_resolution_reason_invalid")
    task_id = str(provider_task_id or "").strip() or None
    if normalized_action == "BIND_PROVIDER_TASK":
        if task_id is None or _PROVIDER_TASK_ID.fullmatch(task_id) is None:
            raise GenerationManualSettlementError("manual_resolution_task_id_invalid")
        if provider_accepted is not None:
            raise GenerationManualSettlementError(
                "manual_resolution_provider_accepted_forbidden"
            )
    else:
        if task_id is not None:
            raise GenerationManualSettlementError(
                "manual_resolution_task_id_forbidden"
            )
        if normalized_action == "CONFIRMED_NOT_ACCEPTED_RETRY":
            if provider_accepted is not False:
                raise GenerationManualSettlementError(
                    "manual_resolution_nonacceptance_required"
                )
        elif provider_accepted is None:
            raise GenerationManualSettlementError(
                "manual_resolution_provider_accepted_required"
            )
    return (
        normalized_action,  # type: ignore[return-value]
        object_key,
        evidence,
        reason,
        task_id,
        provider_accepted,
    )


def build_generation_manual_evidence(
    *,
    job: GenerationJob,
    attempt: GenerationAttempt,
    order: Order,
    action: str,
    source_type: str,
    observation_reference: str,
    observed_at: datetime,
    approval_id: uuid.UUID,
    operator_actor: str,
    operator_reason: str,
    provider_task_id: str | None = None,
    provider_accepted: bool | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bytes, GenerationManualEvidence]:
    """Build one content-addressed operator fact bound to an exact UNKNOWN case."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise GenerationManualSettlementError("manual_resolution_evidence_time_invalid")
    current = current.astimezone(timezone.utc)
    observed = _aware_timestamp(
        observed_at,
        code="manual_resolution_evidence_time_invalid",
    )
    if observed > current + timedelta(minutes=5) or current - observed > _EVIDENCE_MAX_AGE:
        raise GenerationManualSettlementError("manual_resolution_evidence_stale")
    source = str(source_type or "").strip().upper()
    if _EVIDENCE_SOURCE.fullmatch(source) is None:
        raise GenerationManualSettlementError("manual_resolution_evidence_source_invalid")
    reference = str(observation_reference or "").strip()
    actor = str(operator_actor or "").strip()
    if not 4 <= len(reference) <= 256 or not 3 <= len(actor) <= 128:
        raise GenerationManualSettlementError("manual_resolution_evidence_source_invalid")
    normalized_action, _placeholder_key, _placeholder_sha, reason, task_id, accepted = (
        _validate_request(
            action=action,
            provider_evidence_object_key=(
                "operations/generation-manual-evidence/"
                f"{attempt.id}/{'0' * 64}.json"
            ),
            provider_evidence_sha256="0" * 64,
            operator_reason=operator_reason,
            provider_task_id=provider_task_id,
            provider_accepted=provider_accepted,
        )
    )
    if (
        attempt.job_id != job.id
        or order.generation_job_id != job.id
        or GenerationJobStatus(job.status) is not GenerationJobStatus.RECONCILING
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.UNKNOWN
        or attempt.provider != "evolink"
        or attempt.provider_job_id is not None
        or OrderStatus(order.status) is not OrderStatus.UNKNOWN_EXTERNAL_STATE
    ):
        raise GenerationManualSettlementError("manual_generation_case_not_resolvable")
    runtime_bundle_id = str(getattr(job, "runtime_bundle_id", "") or "").strip()
    api_deployment_id = str(getattr(job, "api_deployment_id", "") or "").strip()
    if not runtime_bundle_id or not api_deployment_id:
        raise GenerationManualSettlementError("manual_resolution_evidence_coordinates_invalid")
    payload = {
        "schema": _EVIDENCE_SCHEMA,
        "provider": "evolink",
        "job_id": str(job.id),
        "order_id": str(order.id),
        "attempt_id": str(attempt.id),
        "runtime_bundle_id": runtime_bundle_id,
        "api_deployment_id": api_deployment_id,
        "action": normalized_action,
        "provider_task_id": task_id,
        "provider_accepted": accepted,
        "source_type": source,
        "observation_reference": reference,
        "observed_at": observed.isoformat(),
        "approval_id": str(approval_id),
        "operator_actor": actor,
        "operator_reason": reason,
        "created_at": current.isoformat(),
    }
    raw = _canonical_evidence(payload)
    sha256 = hashlib.sha256(raw).hexdigest()
    object_key = _evidence_object_key(attempt.id, sha256)
    return (
        payload,
        raw,
        GenerationManualEvidence(
            object_key=object_key,
            sha256=sha256,
            job_id=job.id,
            attempt_id=attempt.id,
            action=normalized_action,
        ),
    )


async def create_generation_manual_evidence(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    action: str,
    source_type: str,
    observation_reference: str,
    observed_at: datetime,
    approval_id: uuid.UUID,
    operator_actor: str,
    operator_reason: str,
    provider_task_id: str | None = None,
    provider_accepted: bool | None = None,
    evidence_store: StorageService | None = None,
    now: datetime | None = None,
) -> GenerationManualEvidence:
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
    )
    if job is None:
        raise GenerationManualSettlementError("manual_generation_job_not_found")
    attempt = (
        await db.scalar(
            select(GenerationAttempt)
            .where(GenerationAttempt.id == job.active_attempt_id)
            .with_for_update()
        )
        if job.active_attempt_id is not None
        else None
    )
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if attempt is None or order is None:
        raise GenerationManualSettlementError("manual_generation_case_not_resolvable")
    _payload, raw, evidence = build_generation_manual_evidence(
        job=job,
        attempt=attempt,
        order=order,
        action=action,
        source_type=source_type,
        observation_reference=observation_reference,
        observed_at=observed_at,
        approval_id=approval_id,
        operator_actor=operator_actor,
        operator_reason=operator_reason,
        provider_task_id=provider_task_id,
        provider_accepted=provider_accepted,
        now=now,
    )
    store = evidence_store or StorageService()
    try:
        existing = await asyncio.to_thread(store.read_private, evidence.object_key)
    except FileNotFoundError:
        try:
            await asyncio.to_thread(
                store.put_private,
                evidence.object_key,
                raw,
                "application/json",
            )
        except RuntimeError:
            existing = await asyncio.to_thread(store.read_private, evidence.object_key)
            if existing != raw:
                raise GenerationManualSettlementError(
                    "manual_resolution_evidence_store_conflict"
                )
    else:
        if existing != raw:
            raise GenerationManualSettlementError(
                "manual_resolution_evidence_store_conflict"
            )
    return evidence


async def _read_generation_manual_evidence(
    *,
    object_key: str,
    sha256: str,
    job: GenerationJob,
    attempt: GenerationAttempt,
    order: Order,
    action: str,
    operator_reason: str,
    provider_task_id: str | None,
    provider_accepted: bool | None,
    evidence_store: StorageService | None,
    now: datetime,
) -> dict[str, Any]:
    expected_key = _evidence_object_key(attempt.id, sha256)
    if object_key != expected_key:
        raise GenerationManualSettlementError("manual_resolution_evidence_reference_invalid")
    store = evidence_store or StorageService()
    try:
        raw = await asyncio.to_thread(store.read_private, object_key)
    except FileNotFoundError as exc:
        raise GenerationManualSettlementError(
            "manual_resolution_evidence_not_found"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise GenerationManualSettlementError("manual_resolution_evidence_hash_mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationManualSettlementError(
            "manual_resolution_evidence_invalid"
        ) from exc
    expected_keys = {
        "schema",
        "provider",
        "job_id",
        "order_id",
        "attempt_id",
        "runtime_bundle_id",
        "api_deployment_id",
        "action",
        "provider_task_id",
        "provider_accepted",
        "source_type",
        "observation_reference",
        "observed_at",
        "approval_id",
        "operator_actor",
        "operator_reason",
        "created_at",
    }
    expected_values = {
        "schema": _EVIDENCE_SCHEMA,
        "provider": "evolink",
        "job_id": str(job.id),
        "order_id": str(order.id),
        "attempt_id": str(attempt.id),
        "runtime_bundle_id": str(getattr(job, "runtime_bundle_id", "") or "").strip(),
        "api_deployment_id": str(getattr(job, "api_deployment_id", "") or "").strip(),
        "action": action,
        "provider_task_id": provider_task_id,
        "provider_accepted": provider_accepted,
        "operator_reason": operator_reason,
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or any(payload.get(key) != value for key, value in expected_values.items())
        or _EVIDENCE_SOURCE.fullmatch(str(payload.get("source_type") or "")) is None
        or not 4 <= len(str(payload.get("observation_reference") or "")) <= 256
        or not 3 <= len(str(payload.get("operator_actor") or "")) <= 128
    ):
        raise GenerationManualSettlementError("manual_resolution_evidence_mismatch")
    try:
        uuid.UUID(str(payload["approval_id"]))
    except ValueError as exc:
        raise GenerationManualSettlementError(
            "manual_resolution_evidence_approval_invalid"
        ) from exc
    observed = _aware_timestamp(
        payload["observed_at"],
        code="manual_resolution_evidence_time_invalid",
    )
    created = _aware_timestamp(
        payload["created_at"],
        code="manual_resolution_evidence_time_invalid",
    )
    if (
        observed > created + timedelta(minutes=5)
        or created > now + timedelta(minutes=5)
        or now - observed > _EVIDENCE_MAX_AGE
    ):
        raise GenerationManualSettlementError("manual_resolution_evidence_stale")
    return payload


async def list_generation_manual_cases(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> list[GenerationManualCase]:
    """Return only task-ID-less ambiguous submissions needing an operator fact."""

    bounded = max(1, min(int(limit), 200))
    rows = (
        await db.execute(
            select(GenerationJob, GenerationAttempt, Order)
            .join(Order, Order.id == GenerationJob.order_id)
            .join(
                GenerationAttempt,
                GenerationAttempt.id == GenerationJob.active_attempt_id,
            )
            .where(
                GenerationJob.status == GenerationJobStatus.RECONCILING,
                GenerationAttempt.status == GenerationAttemptStatus.UNKNOWN,
                GenerationAttempt.provider_job_id.is_(None),
                Order.status == OrderStatus.UNKNOWN_EXTERNAL_STATE,
            )
            .order_by(
                GenerationAttempt.submit_started_at.asc().nullsfirst(),
                GenerationJob.id.asc(),
            )
            .limit(bounded)
        )
    ).all()
    return [
        GenerationManualCase(
            job_id=job.id,
            order_id=order.id,
            attempt_id=attempt.id,
            attempt_kind=_status_value(attempt.kind),
            submit_started_at=attempt.submit_started_at,
            reason_code=str(
                job.last_error_code or "provider_submission_human_required"
            ),
            reason_detail=job.last_error_detail,
        )
        for job, attempt, order in rows
    ]


async def count_generation_manual_cases(db: AsyncSession) -> int:
    """Count the durable human queue without making it eligible for auto-submit."""

    return int(
        await db.scalar(
            select(func.count(GenerationJob.id))
            .join(Order, Order.id == GenerationJob.order_id)
            .join(
                GenerationAttempt,
                GenerationAttempt.id == GenerationJob.active_attempt_id,
            )
            .where(
                GenerationJob.status == GenerationJobStatus.RECONCILING,
                GenerationAttempt.status == GenerationAttemptStatus.UNKNOWN,
                GenerationAttempt.provider_job_id.is_(None),
                Order.status == OrderStatus.UNKNOWN_EXTERNAL_STATE,
            )
        )
        or 0
    )


async def resolve_generation_manual_case(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    action: str,
    provider_evidence_object_key: str,
    provider_evidence_sha256: str,
    operator_reason: str,
    provider_task_id: str | None = None,
    provider_accepted: bool | None = None,
    evidence_store: StorageService | None = None,
    now: datetime | None = None,
) -> GenerationManualResolution:
    """Apply one evidence-bound resolution; ambiguous Provider POST is never replayed."""

    (
        normalized_action,
        evidence_object_key,
        evidence_sha256,
        reason,
        task_id,
        accepted,
    ) = _validate_request(
        action=action,
        provider_evidence_object_key=provider_evidence_object_key,
        provider_evidence_sha256=provider_evidence_sha256,
        operator_reason=operator_reason,
        provider_task_id=provider_task_id,
        provider_accepted=provider_accepted,
    )
    current = now or datetime.now(timezone.utc)
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
    )
    if job is None:
        raise GenerationManualSettlementError("manual_generation_job_not_found")
    attempt = (
        await db.scalar(
            select(GenerationAttempt)
            .where(GenerationAttempt.id == job.active_attempt_id)
            .with_for_update()
        )
        if job.active_attempt_id is not None
        else None
    )
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if (
        attempt is None
        or order is None
        or attempt.job_id != job.id
        or order.generation_job_id != job.id
        or GenerationJobStatus(job.status) is not GenerationJobStatus.RECONCILING
        or GenerationAttemptStatus(attempt.status)
        is not GenerationAttemptStatus.UNKNOWN
        or attempt.provider_job_id is not None
        or OrderStatus(order.status) is not OrderStatus.UNKNOWN_EXTERNAL_STATE
        or job.lease_owner is not None
        or job.lease_claim_id is not None
        or job.lease_expires_at is not None
    ):
        raise GenerationManualSettlementError(
            "manual_generation_case_not_resolvable"
        )
    await _read_generation_manual_evidence(
        object_key=evidence_object_key,
        sha256=evidence_sha256,
        job=job,
        attempt=attempt,
        order=order,
        action=normalized_action,
        operator_reason=reason,
        provider_task_id=task_id,
        provider_accepted=accepted,
        evidence_store=evidence_store,
        now=current,
    )
    if order.reservation_id is None:
        raise GenerationManualSettlementError(
            "manual_generation_reservation_missing"
        )
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == order.reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise GenerationManualSettlementError(
            "manual_generation_reservation_missing"
        )
    attempt_kind = GenerationAttemptKind(attempt.kind)

    if normalized_action == "BIND_PROVIDER_TASK":
        existing_attempt_id = await db.scalar(
            select(GenerationAttempt.id).where(
                GenerationAttempt.provider == attempt.provider,
                GenerationAttempt.provider_job_id == task_id,
                GenerationAttempt.id != attempt.id,
            )
        )
        if existing_attempt_id is not None:
            raise GenerationManualSettlementError(
                "provider_task_already_bound"
            )
        try:
            async with db.begin_nested():
                attempt.provider_job_id = task_id
                attempt.status = validate_attempt_transition(
                    attempt.status,
                    GenerationAttemptStatus.SUBMITTED,
                )
                attempt.submitted_at = attempt.submitted_at or current
                if attempt_kind is GenerationAttemptKind.INITIAL:
                    if (
                        ReservationStatus(reservation.status)
                        is not ReservationStatus.RESERVED
                    ):
                        raise GenerationManualSettlementError(
                            "manual_generation_reservation_not_reserved"
                        )
                    attempt.submission_accounting_state = "PENDING"
                elif (
                    ReservationStatus(reservation.status)
                    is not ReservationStatus.CAPTURED
                ):
                    raise GenerationManualSettlementError(
                        "manual_generation_repair_capture_missing"
                    )
                job.next_retry_at = current
                job.last_error_code = "provider_task_bound_by_operator"
                job.last_error_detail = (
                    f"evidence:{evidence_sha256}; reason:{reason}"
                )
                order.status = (
                    OrderStatus.GENERATING
                    if attempt_kind is GenerationAttemptKind.INITIAL
                    else OrderStatus.REPAIRING
                )
                await db.flush()
        except IntegrityError as exc:
            if _provider_task_unique_conflict(exc):
                raise GenerationManualSettlementError(
                    "provider_task_already_bound"
                ) from exc
            raise
        next_action = "RECONCILE_PROVIDER_TASK"
    elif normalized_action == "CONFIRMED_NOT_ACCEPTED_RETRY":
        expected_reservation = (
            ReservationStatus.RESERVED
            if attempt_kind is GenerationAttemptKind.INITIAL
            else ReservationStatus.CAPTURED
        )
        if ReservationStatus(reservation.status) is not expected_reservation:
            raise GenerationManualSettlementError(
                "manual_generation_retry_accounting_invalid"
            )
        grants = list(
            (
                await db.scalars(
                    select(AssetAccessGrant)
                    .where(AssetAccessGrant.attempt_id == attempt.id)
                    .with_for_update()
                )
            ).all()
        )
        for grant in grants:
            if grant.revoked_at is None:
                grant.revoked_at = current
        attempt.status = validate_attempt_transition(
            attempt.status,
            GenerationAttemptStatus.PREPARED,
        )
        attempt.submit_started_at = None
        attempt.submitted_at = None
        attempt.cost_minor_units = None
        attempt.cost_currency = None
        attempt.submission_accounting_state = "NOT_CAPTURED"
        job.status = validate_job_transition(
            job.status,
            GenerationJobStatus.ACTIVE,
        )
        job.next_retry_at = current
        job.retry_count = int(job.retry_count or 0) + 1
        job.last_error_code = "operator_confirmed_not_accepted_retry"
        job.last_error_detail = f"evidence:{evidence_sha256}; reason:{reason}"
        order.status = (
            OrderStatus.GENERATING
            if attempt_kind is GenerationAttemptKind.INITIAL
            else OrderStatus.REPAIRING
        )
        next_action = "ONE_OPERATOR_AUTHORIZED_SUBMISSION"
    else:
        grants = list(
            (
                await db.scalars(
                    select(AssetAccessGrant)
                    .where(AssetAccessGrant.attempt_id == attempt.id)
                    .with_for_update()
                )
            ).all()
        )
        for grant in grants:
            if grant.revoked_at is None:
                grant.revoked_at = current

        reservation_status = ReservationStatus(reservation.status)
        if reservation_status is ReservationStatus.RESERVED and accepted is True:
            if attempt_kind is not GenerationAttemptKind.INITIAL:
                raise GenerationManualSettlementError(
                    "manual_generation_acceptance_lineage_invalid"
                )
            attempt.status = validate_attempt_transition(
                attempt.status,
                GenerationAttemptStatus.SUBMITTED,
            )
            attempt.submitted_at = attempt.submitted_at or current
            attempt.submission_accounting_state = "PENDING"
            await capture_reservation(
                db,
                reservation_id=reservation.id,
                provider_attempt_id=attempt.id,
                idempotency_key=f"capture:{attempt.id}",
                now=current,
            )
            attempt.submission_accounting_state = "CAPTURED"
            reservation_status = ReservationStatus.CAPTURED
        if reservation_status is ReservationStatus.RESERVED:
            if accepted is not False:
                raise GenerationManualSettlementError(
                    "manual_generation_nonacceptance_evidence_required"
                )
            settlement = "RELEASE"
            settlement_status = "RELEASED"
        elif reservation_status is ReservationStatus.CAPTURED:
            settlement = "GENERATION_REFUND"
            settlement_status = "REFUNDED"
        else:
            raise GenerationManualSettlementError(
                "manual_generation_settlement_state_invalid"
            )
        if GenerationAttemptStatus(attempt.status) is GenerationAttemptStatus.UNKNOWN:
            attempt.status = validate_attempt_transition(
                attempt.status,
                GenerationAttemptStatus.FAILED,
            )
        elif GenerationAttemptStatus(attempt.status) is GenerationAttemptStatus.SUBMITTED:
            attempt.status = validate_attempt_transition(
                attempt.status,
                GenerationAttemptStatus.FAILED,
            )
        else:
            raise GenerationManualSettlementError(
                "manual_generation_attempt_settlement_invalid"
            )
        attempt.finished_at = current
        await release_or_refund_reservation(
            db,
            reservation_id=reservation.id,
            settlement=settlement,
            idempotency_key=f"manual-generation-settlement:{job.id}",
            pre_submission_confirmed=settlement == "RELEASE",
            reason_code="operator_provider_resolution",
            now=current,
        )
        job.status = validate_job_transition(job.status, GenerationJobStatus.FAILED)
        job.next_retry_at = None
        job.settlement_status = settlement_status
        job.delivery_status = "BLOCKED"
        job.last_error_code = "operator_provider_resolution"
        job.last_error_detail = f"evidence:{evidence_sha256}; reason:{reason}"
        job.finished_at = current
        order.status = OrderStatus.FAILED
        order.settlement_status = settlement_status
        order.delivery_status = "BLOCKED"
        order.error_message = "operator_provider_resolution"
        next_action = "TERMINAL"

    return GenerationManualResolution(
        job_id=job.id,
        order_id=order.id,
        attempt_id=attempt.id,
        action=normalized_action,
        job_status=_status_value(job.status),
        attempt_status=_status_value(attempt.status),
        order_status=_status_value(order.status),
        settlement_status=str(job.settlement_status),
        next_action=next_action,
    )
