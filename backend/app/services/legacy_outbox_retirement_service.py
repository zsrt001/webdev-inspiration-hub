"""Fail-closed retirement of pre-backend generation/payment outbox envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import GenerationAttempt
from app.models.generation_job import GenerationJob
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.payment_event import PaymentEvent


ACTIVE_OUTBOX_STATUSES = (
    OutboxEventStatus.PENDING,
    OutboxEventStatus.PROCESSING,
    OutboxEventStatus.FAILED,
)
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True, slots=True)
class LegacyOutboxDecision:
    event_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    outbox_status: str
    underlying_status: str | None
    disposition: str
    reason: str

    @property
    def retirable(self) -> bool:
        return self.disposition == "RETIRE"

    def evidence(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "event_type": self.event_type,
            "outbox_status": self.outbox_status,
            "underlying_status": self.underlying_status,
            "disposition": self.disposition,
            "reason": self.reason,
        }


def _value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _payload_identity(
    event: OutboxEvent,
    *,
    key: str,
    payload_version: str,
) -> uuid.UUID | None:
    payload = event.payload_json
    if (
        event.payload_version != payload_version
        or not isinstance(payload, dict)
        or set(payload) != {key, "payload_version"}
        or payload.get("payload_version") != payload_version
    ):
        return None
    try:
        entity_id = uuid.UUID(str(payload.get(key)))
    except (TypeError, ValueError, AttributeError):
        return None
    return entity_id if entity_id == event.aggregate_id else None


def _decision(
    event: OutboxEvent,
    *,
    fact_status: str | None,
    retirable: bool,
    reason: str,
) -> LegacyOutboxDecision:
    return LegacyOutboxDecision(
        event_id=event.id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        event_type=event.event_type,
        outbox_status=_value(event.status),
        underlying_status=fact_status,
        disposition="RETIRE" if retirable else "BLOCK",
        reason=reason,
    )


def classify_legacy_outbox_event(
    event: OutboxEvent,
    fact: GenerationJob | GenerationAttempt | PaymentEvent | None,
) -> LegacyOutboxDecision:
    """Classify one envelope only from its exact v1 contract and authority row."""

    if (
        event.aggregate_type == "generation_job"
        and event.event_type == "GENERATION_JOB_CREATED"
    ):
        entity_id = _payload_identity(
            event,
            key="job_id",
            payload_version="generation-job.v1",
        )
        if entity_id is None:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="generation_job_envelope_invalid",
            )
        if fact is None or getattr(fact, "id", None) != entity_id:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="generation_job_missing",
            )
        status = _value(getattr(fact, "status", None)).upper()
        settlement = _value(getattr(fact, "settlement_status", None)).upper()
        delivery = _value(getattr(fact, "delivery_status", None)).upper()
        if status in {"ACTIVE", "RECONCILING"}:
            return _decision(
                event,
                fact_status=status,
                retirable=True,
                reason="generation_job_claimed_by_durable_executor",
            )
        if status == "FINISHED" and settlement == "CAPTURED" and delivery in {
            "READY",
            "PUBLISHED",
            "REVOKED",
        }:
            return _decision(
                event,
                fact_status=f"{status}:{settlement}:{delivery}",
                retirable=True,
                reason="generation_job_terminal_delivery_accounted",
            )
        if status in {"FAILED", "CANCELLED"} and settlement in {
            "RELEASED",
            "REFUNDED",
        } and delivery in {"BLOCKED", "REVOKED", "NOT_DELIVERED"}:
            return _decision(
                event,
                fact_status=f"{status}:{settlement}:{delivery}",
                retirable=True,
                reason="generation_job_terminal_failure_accounted",
            )
        return _decision(
            event,
            fact_status=f"{status}:{settlement}:{delivery}",
            retirable=False,
            reason="generation_job_requires_backend_recovery_or_manual_settlement",
        )

    if (
        event.aggregate_type == "generation_attempt"
        and event.event_type == "GENERATION_ATTEMPT_CREATED"
    ):
        entity_id = _payload_identity(
            event,
            key="attempt_id",
            payload_version="generation-attempt.v1",
        )
        if entity_id is None:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="generation_attempt_envelope_invalid",
            )
        if fact is None or getattr(fact, "id", None) != entity_id:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="generation_attempt_missing",
            )
        status = _value(getattr(fact, "status", None)).upper()
        if status in {"SUBMITTED", "FINISHED", "FAILED"}:
            return _decision(
                event,
                fact_status=status,
                retirable=True,
                reason="generation_attempt_submission_accounted",
            )
        return _decision(
            event,
            fact_status=status,
            retirable=False,
            reason="generation_attempt_requires_backend_recovery_or_manual_settlement",
        )

    if (
        event.aggregate_type == "payment_event"
        and event.event_type == "payment.event.received"
    ):
        entity_id = _payload_identity(
            event,
            key="payment_event_id",
            payload_version="vowpic.payment-event.v1",
        )
        if entity_id is None:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="payment_event_envelope_invalid",
            )
        if fact is None or getattr(fact, "id", None) != entity_id:
            return _decision(
                event,
                fact_status=None,
                retirable=False,
                reason="payment_event_missing",
            )
        status = _value(getattr(fact, "processing_state", None)).upper()
        return _decision(
            event,
            fact_status=status,
            retirable=status == "APPLIED",
            reason=(
                "payment_event_applied"
                if status == "APPLIED"
                else "payment_event_requires_reconciliation"
            ),
        )

    return _decision(
        event,
        fact_status=None,
        retirable=False,
        reason="unknown_legacy_outbox_contract",
    )


async def _scan(
    db: AsyncSession,
    *,
    lock: bool,
) -> list[tuple[OutboxEvent, LegacyOutboxDecision]]:
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status.in_(ACTIVE_OUTBOX_STATUSES))
        .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
    )
    if lock:
        statement = statement.with_for_update()
    events = list((await db.scalars(statement)).all())
    scanned: list[tuple[OutboxEvent, LegacyOutboxDecision]] = []
    for event in events:
        if event.aggregate_type == "generation_job":
            fact = await db.get(GenerationJob, event.aggregate_id)
        elif event.aggregate_type == "generation_attempt":
            fact = await db.get(GenerationAttempt, event.aggregate_id)
        elif event.aggregate_type == "payment_event":
            fact = await db.get(PaymentEvent, event.aggregate_id)
        else:
            fact = None
        scanned.append((event, classify_legacy_outbox_event(event, fact)))
    return scanned


def _snapshot_sha256(decisions: list[LegacyOutboxDecision]) -> str:
    raw = json.dumps(
        [decision.evidence() for decision in decisions],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _report(
    *,
    source_sha: str,
    mode: str,
    decisions: list[LegacyOutboxDecision],
    retired_event_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    retired = retired_event_ids or []
    blocked = [item for item in decisions if not item.retirable]
    retirable = [item for item in decisions if item.retirable]
    return {
        "schema": "vowpic.legacy-outbox-retirement.v1",
        "mode": mode,
        "source_sha": source_sha,
        "snapshot_sha256": _snapshot_sha256(decisions),
        "active_count": len(decisions),
        "retirable_count": len(retirable),
        "blocked_count": len(blocked),
        "retired_event_ids": [str(item) for item in sorted(retired, key=str)],
        "passed": len(blocked) == 0,
        "decisions": [item.evidence() for item in decisions],
    }


async def inventory_legacy_outbox(
    db: AsyncSession,
    *,
    source_sha: str,
) -> dict[str, Any]:
    normalized_sha = str(source_sha or "").strip().lower()
    if not _SOURCE_SHA.fullmatch(normalized_sha):
        raise ValueError("legacy outbox source SHA is invalid")
    scanned = await _scan(db, lock=False)
    return _report(
        source_sha=normalized_sha,
        mode="inventory",
        decisions=[decision for _event, decision in scanned],
    )


async def retire_legacy_outbox(
    db: AsyncSession,
    *,
    source_sha: str,
    expected_snapshot_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_sha = str(source_sha or "").strip().lower()
    if not _SOURCE_SHA.fullmatch(normalized_sha):
        raise ValueError("legacy outbox source SHA is invalid")
    if not re.fullmatch(r"^[0-9a-f]{64}$", expected_snapshot_sha256 or ""):
        raise ValueError("legacy outbox inventory SHA is invalid")

    scanned = await _scan(db, lock=True)
    decisions = [decision for _event, decision in scanned]
    if _snapshot_sha256(decisions) != expected_snapshot_sha256:
        raise ValueError("legacy outbox inventory changed before retirement")

    current = now or datetime.now(timezone.utc)
    retired: list[uuid.UUID] = []
    for event, decision in scanned:
        if not decision.retirable:
            continue
        event.status = OutboxEventStatus.DISPATCHED
        event.lease_owner = None
        event.lease_claim_id = None
        event.lease_expires_at = None
        event.dispatched_at = event.dispatched_at or current
        event.updated_at = current
        event.last_error = (
            f"legacy_retired:{decision.reason}:{normalized_sha[:12]}"
        )
        retired.append(event.id)
    await db.flush()

    remaining = await _scan(db, lock=True)
    remaining_decisions = [decision for _event, decision in remaining]
    report = _report(
        source_sha=normalized_sha,
        mode="apply",
        decisions=remaining_decisions,
        retired_event_ids=retired,
    )
    report["inventory_snapshot_sha256"] = expected_snapshot_sha256
    return report
