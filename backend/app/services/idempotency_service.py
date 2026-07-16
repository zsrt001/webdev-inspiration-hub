"""Transaction-scoped request idempotency backed by PostgreSQL facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_record import IdempotencyRecord, IdempotencyState


IDEMPOTENCY_TTL = timedelta(days=7)


class IdempotencyConflict(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IdempotencyAttempt:
    record_id: uuid.UUID
    replayed: bool
    state: str
    response_status: int | None
    response_json: dict | None


def canonical_request_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _clean(value: str, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field}_invalid")
    return normalized


def _state(value: IdempotencyState | str) -> str:
    return value.value if isinstance(value, IdempotencyState) else str(value)


async def lock_idempotency_scope(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    key: str,
) -> None:
    scope = f"{user_id}:{endpoint}:{key}".encode("utf-8")
    lock_key = int.from_bytes(sha256(scope).digest()[:8], "big", signed=True)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def _attempt(record: IdempotencyRecord, *, replayed: bool) -> IdempotencyAttempt:
    return IdempotencyAttempt(
        record_id=record.id,
        replayed=replayed,
        state=_state(record.state),
        response_status=record.response_status,
        response_json=dict(record.response_json) if record.response_json is not None else None,
    )


async def begin_idempotent_request(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    key: str,
    request_hash: str,
    now: datetime | None = None,
) -> IdempotencyAttempt:
    if not isinstance(user_id, uuid.UUID):
        user_id = uuid.UUID(str(user_id))
    clean_endpoint = _clean(endpoint, field="endpoint", maximum=128)
    clean_key = _clean(key, field="idempotency_key", maximum=128)
    clean_hash = _clean(request_hash, field="request_hash", maximum=64)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("idempotency_now_must_be_timezone_aware")

    await lock_idempotency_scope(
        db,
        user_id=user_id,
        endpoint=clean_endpoint,
        key=clean_key,
    )
    existing = await db.scalar(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.endpoint == clean_endpoint,
            IdempotencyRecord.idempotency_key == clean_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.request_hash != clean_hash:
            raise IdempotencyConflict("idempotency_payload_mismatch")
        return _attempt(existing, replayed=True)

    record = IdempotencyRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        endpoint=clean_endpoint,
        idempotency_key=clean_key,
        request_hash=clean_hash,
        state=IdempotencyState.STARTED,
        expires_at=current + IDEMPOTENCY_TTL,
    )
    db.add(record)
    await db.flush()
    return _attempt(record, replayed=False)


async def complete_idempotent_request(
    db: AsyncSession,
    *,
    record_id: uuid.UUID,
    response_status: int,
    response_json: dict,
) -> IdempotencyRecord:
    if not 100 <= int(response_status) <= 599:
        raise ValueError("idempotency_response_status_invalid")
    record = await db.scalar(
        select(IdempotencyRecord)
        .where(IdempotencyRecord.id == record_id)
        .with_for_update()
    )
    if record is None:
        raise LookupError("idempotency_record_not_found")
    if _state(record.state) == IdempotencyState.COMPLETED.value:
        if (
            int(record.response_status or 0) != int(response_status)
            or dict(record.response_json or {}) != dict(response_json)
        ):
            raise IdempotencyConflict("idempotency_completion_mismatch")
        return record
    if _state(record.state) != IdempotencyState.STARTED.value:
        raise IdempotencyConflict("idempotency_not_completable")
    record.state = IdempotencyState.COMPLETED
    record.response_status = int(response_status)
    record.response_json = dict(response_json)
    await db.flush()
    return record


async def fail_idempotent_request(
    db: AsyncSession,
    *,
    record_id: uuid.UUID,
    response_status: int,
    response_json: dict,
) -> IdempotencyRecord:
    record = await db.scalar(
        select(IdempotencyRecord)
        .where(IdempotencyRecord.id == record_id)
        .with_for_update()
    )
    if record is None:
        raise LookupError("idempotency_record_not_found")
    if _state(record.state) != IdempotencyState.STARTED.value:
        raise IdempotencyConflict("idempotency_not_failable")
    record.state = IdempotencyState.FAILED
    record.response_status = int(response_status)
    record.response_json = dict(response_json)
    await db.flush()
    return record
