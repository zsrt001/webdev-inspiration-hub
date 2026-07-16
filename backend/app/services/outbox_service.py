"""Crash-safe publication of IDs-only generation outbox facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_job import GENERATION_JOB_PAYLOAD_VERSION
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.services.generation_repair_service import (
    GENERATION_ATTEMPT_AGGREGATE_TYPE,
    GENERATION_ATTEMPT_CREATED_EVENT,
    GENERATION_ATTEMPT_PAYLOAD_VERSION,
)


GENERATION_AGGREGATE_TYPE = "generation_job"
GENERATION_CREATED_EVENT = "GENERATION_JOB_CREATED"
GENERATION_FUNCTION_NAME = "generate_order_v1"
GENERATION_ATTEMPT_FUNCTION_NAME = "generate_attempt_v1"
MAX_OUTBOX_ATTEMPTS = 3


class GenerationOutboxContractError(RuntimeError):
    """The durable event cannot be safely interpreted by the v1 Worker."""


@dataclass(frozen=True, slots=True)
class GenerationJobMessage:
    job_id: uuid.UUID
    payload_version: str

    @property
    def function_name(self) -> str:
        return GENERATION_FUNCTION_NAME

    @property
    def redis_job_id(self) -> str:
        return f"generation:v1:{self.job_id}"


@dataclass(frozen=True, slots=True)
class GenerationAttemptMessage:
    attempt_id: uuid.UUID
    payload_version: str

    @property
    def function_name(self) -> str:
        return GENERATION_ATTEMPT_FUNCTION_NAME

    @property
    def redis_job_id(self) -> str:
        return f"generation-attempt:v1:{self.attempt_id}"


@dataclass(frozen=True, slots=True)
class PublishResult:
    dispatched_event_ids: tuple[uuid.UUID, ...]
    retry_event_ids: tuple[uuid.UUID, ...]
    failed_event_ids: tuple[uuid.UUID, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generation_message_from_event(
    event: OutboxEvent,
) -> GenerationJobMessage | GenerationAttemptMessage:
    """Validate every persisted field before constructing the Redis message."""
    payload = event.payload_json
    if event.event_type == GENERATION_CREATED_EVENT:
        if event.aggregate_type != GENERATION_AGGREGATE_TYPE:
            raise GenerationOutboxContractError("generation_outbox_aggregate_invalid")
        if event.payload_version != GENERATION_JOB_PAYLOAD_VERSION:
            raise GenerationOutboxContractError("generation_outbox_payload_version_invalid")
        if not isinstance(payload, dict) or set(payload) != {"job_id", "payload_version"}:
            raise GenerationOutboxContractError("generation_outbox_payload_not_ids_only")
        if payload.get("payload_version") != GENERATION_JOB_PAYLOAD_VERSION:
            raise GenerationOutboxContractError("generation_outbox_payload_version_mismatch")
        try:
            job_id = uuid.UUID(str(payload.get("job_id")))
        except (TypeError, ValueError, AttributeError) as exc:
            raise GenerationOutboxContractError("generation_outbox_job_id_invalid") from exc
        expected_dedupe = f"generation:v1:{job_id}"
        if job_id != event.aggregate_id or event.dedupe_key != expected_dedupe:
            raise GenerationOutboxContractError("generation_outbox_identity_mismatch")
        return GenerationJobMessage(
            job_id=job_id,
            payload_version=GENERATION_JOB_PAYLOAD_VERSION,
        )

    if event.event_type == GENERATION_ATTEMPT_CREATED_EVENT:
        if event.aggregate_type != GENERATION_ATTEMPT_AGGREGATE_TYPE:
            raise GenerationOutboxContractError("generation_attempt_outbox_aggregate_invalid")
        if event.payload_version != GENERATION_ATTEMPT_PAYLOAD_VERSION:
            raise GenerationOutboxContractError("generation_attempt_outbox_version_invalid")
        if not isinstance(payload, dict) or set(payload) != {"attempt_id", "payload_version"}:
            raise GenerationOutboxContractError("generation_attempt_outbox_payload_not_ids_only")
        if payload.get("payload_version") != GENERATION_ATTEMPT_PAYLOAD_VERSION:
            raise GenerationOutboxContractError("generation_attempt_outbox_version_mismatch")
        try:
            attempt_id = uuid.UUID(str(payload.get("attempt_id")))
        except (TypeError, ValueError, AttributeError) as exc:
            raise GenerationOutboxContractError("generation_attempt_outbox_id_invalid") from exc
        expected_dedupe = f"generation-attempt:v1:{attempt_id}"
        if attempt_id != event.aggregate_id or event.dedupe_key != expected_dedupe:
            raise GenerationOutboxContractError("generation_attempt_outbox_identity_mismatch")
        return GenerationAttemptMessage(
            attempt_id=attempt_id,
            payload_version=GENERATION_ATTEMPT_PAYLOAD_VERSION,
        )

    raise GenerationOutboxContractError("generation_outbox_event_invalid")


async def publish_pending_generation_outbox(
    db: AsyncSession,
    redis: ArqRedis,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> PublishResult:
    """Publish due rows while their PostgreSQL locks exclude peer dispatchers.

    The caller owns the transaction. A crash before commit rolls the row back to
    PENDING; a crash after Redis accepted the deterministic job ID is harmless
    because ARQ returns ``None`` for the duplicate ID on the next dispatch.
    """
    current = now or _utcnow()
    bounded_limit = max(1, min(int(limit), 500))
    rows = list(
        (
            await db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OutboxEventStatus.PENDING,
                    OutboxEvent.next_attempt_at <= current,
                    OutboxEvent.event_type.in_(
                        (GENERATION_CREATED_EVENT, GENERATION_ATTEMPT_CREATED_EVENT)
                    ),
                )
                .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
                .with_for_update(skip_locked=True)
                .limit(bounded_limit)
            )
        ).all()
    )
    dispatched: list[uuid.UUID] = []
    retrying: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for event in rows:
        if event.status != OutboxEventStatus.PENDING:
            continue
        try:
            message = generation_message_from_event(event)
            entity_id = (
                message.job_id
                if isinstance(message, GenerationJobMessage)
                else message.attempt_id
            )
            await redis.enqueue_job(
                message.function_name,
                str(entity_id),
                message.payload_version,
                _job_id=message.redis_job_id,
            )
        except GenerationOutboxContractError:
            event.attempt_count = MAX_OUTBOX_ATTEMPTS
            event.status = OutboxEventStatus.FAILED
            event.last_error = "GenerationOutboxContractError"
            failed.append(event.id)
            continue
        except Exception as exc:
            event.attempt_count = int(event.attempt_count or 0) + 1
            event.last_error = type(exc).__name__
            if event.attempt_count >= MAX_OUTBOX_ATTEMPTS:
                event.status = OutboxEventStatus.FAILED
                failed.append(event.id)
            else:
                delay_seconds = 2 ** event.attempt_count
                event.next_attempt_at = current + timedelta(seconds=delay_seconds)
                retrying.append(event.id)
            continue
        event.attempt_count = int(event.attempt_count or 0) + 1
        event.status = OutboxEventStatus.DISPATCHED
        event.dispatched_at = current
        event.last_error = None
        dispatched.append(event.id)
    return PublishResult(
        dispatched_event_ids=tuple(dispatched),
        retry_event_ids=tuple(retrying),
        failed_event_ids=tuple(failed),
    )
