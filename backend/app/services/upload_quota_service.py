"""PostgreSQL-authoritative upload request, byte, and concurrent-slot quotas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.upload_batch import UploadBatch, UploadBatchStatus
from app.models.upload_quota_reservation import (
    UploadQuotaReservation,
    UploadQuotaReservationStatus,
)
from app.models.upload_quota_state import UploadQuotaState
from app.models.upload_quota_window import UploadQuotaWindow, UploadQuotaWindowKind


settings = get_settings()


class UploadQuotaExceeded(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("upload quota time must be timezone-aware")
    return current.astimezone(timezone.utc)


def _window_start(now: datetime, kind: UploadQuotaWindowKind) -> datetime:
    if kind is UploadQuotaWindowKind.HOURLY_REQUESTS:
        return now.replace(minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _ensure_state(db: AsyncSession, user_id: uuid.UUID) -> UploadQuotaState:
    await db.execute(
        pg_insert(UploadQuotaState)
        .values(user_id=user_id, active_slots=0, version=0)
        .on_conflict_do_nothing(index_elements=[UploadQuotaState.user_id])
    )
    result = await db.execute(
        select(UploadQuotaState)
        .where(UploadQuotaState.user_id == user_id)
        .with_for_update()
    )
    return result.scalar_one()


async def _ensure_window(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: UploadQuotaWindowKind,
    now: datetime,
) -> UploadQuotaWindow:
    start = _window_start(now, kind)
    await db.execute(
        pg_insert(UploadQuotaWindow)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            window_kind=kind.value,
            window_start=start,
            request_count=0,
            attempted_bytes=0,
            reserved_bytes=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                UploadQuotaWindow.user_id,
                UploadQuotaWindow.window_kind,
                UploadQuotaWindow.window_start,
            ]
        )
    )
    result = await db.execute(
        select(UploadQuotaWindow)
        .where(
            UploadQuotaWindow.user_id == user_id,
            UploadQuotaWindow.window_kind == kind.value,
            UploadQuotaWindow.window_start == start,
        )
        .with_for_update()
    )
    return result.scalar_one()


async def create_upload_batch(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    request_id: str,
    now: datetime | None = None,
) -> UploadBatch:
    """Reserve one request and one concurrent slot before reading body bytes."""

    current = _aware_now(now)
    try:
        state = await _ensure_state(db, user_id)
        hourly = await _ensure_window(
            db,
            user_id=user_id,
            kind=UploadQuotaWindowKind.HOURLY_REQUESTS,
            now=current,
        )
        if hourly.request_count >= int(settings.upload_requests_per_hour):
            raise UploadQuotaExceeded("upload_hourly_request_limit")
        if state.active_slots >= int(settings.upload_max_concurrent):
            raise UploadQuotaExceeded("upload_concurrent_limit")

        hourly.request_count += 1
        state.active_slots += 1
        state.version += 1
        batch = UploadBatch(
            id=uuid.uuid4(),
            owner_user_id=user_id,
            status=UploadBatchStatus.PENDING_UPLOAD,
            request_id=str(request_id or "unknown")[:128],
            expected_files=int(settings.upload_max_files),
            received_files=0,
            expires_at=current + timedelta(seconds=int(settings.upload_intent_ttl_seconds)),
            lease_expires_at=current + timedelta(seconds=int(settings.upload_intent_ttl_seconds)),
        )
        db.add(batch)
        await db.commit()
        return batch
    except UploadQuotaExceeded:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


async def reserve_upload_part(
    db: AsyncSession,
    *,
    batch: UploadBatch,
    part_ordinal: int,
    now: datetime | None = None,
) -> UploadQuotaReservation:
    """Reserve the full per-file maximum in the exact UTC daily byte bucket."""

    current = _aware_now(now)
    if part_ordinal < 0 or part_ordinal >= int(settings.upload_max_files):
        raise UploadQuotaExceeded("upload_file_count_limit")
    existing_result = await db.execute(
        select(UploadQuotaReservation)
        .where(
            UploadQuotaReservation.batch_id == batch.id,
            UploadQuotaReservation.part_ordinal == part_ordinal,
        )
        .with_for_update()
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return existing

    daily = await _ensure_window(
        db,
        user_id=batch.owner_user_id,
        kind=UploadQuotaWindowKind.DAILY_BYTES,
        now=current,
    )
    reserve_bytes = int(settings.upload_max_bytes)
    projected = int(daily.attempted_bytes) + int(daily.reserved_bytes) + reserve_bytes
    if projected > int(settings.upload_bytes_per_day):
        await db.rollback()
        raise UploadQuotaExceeded("upload_daily_byte_limit")

    reservation = UploadQuotaReservation(
        id=uuid.uuid4(),
        batch_id=batch.id,
        quota_window_id=daily.id,
        part_ordinal=part_ordinal,
        reserved_bytes=reserve_bytes,
        actual_attempted_bytes=0,
        status=UploadQuotaReservationStatus.RESERVED,
    )
    daily.reserved_bytes += reserve_bytes
    db.add(reservation)
    await db.commit()
    return reservation


async def record_upload_progress(
    db: AsyncSession,
    reservation: UploadQuotaReservation,
    actual_attempted_bytes: int,
) -> None:
    """Persist monotonic progress so a stale lease can settle after a crash."""

    actual = max(0, int(actual_attempted_bytes))
    if reservation.status != UploadQuotaReservationStatus.RESERVED:
        return
    if actual <= int(reservation.actual_attempted_bytes):
        return
    reservation.actual_attempted_bytes = actual
    await db.commit()


async def settle_upload_part(
    db: AsyncSession,
    reservation: UploadQuotaReservation,
    *,
    actual_attempted_bytes: int,
    now: datetime | None = None,
) -> None:
    """Move reserved bytes to attempted bytes exactly once."""

    current = _aware_now(now)
    if reservation.status != UploadQuotaReservationStatus.RESERVED:
        return
    result = await db.execute(
        select(UploadQuotaWindow)
        .where(UploadQuotaWindow.id == reservation.quota_window_id)
        .with_for_update()
    )
    window = result.scalar_one()
    actual = max(int(reservation.actual_attempted_bytes), int(actual_attempted_bytes), 0)
    window.reserved_bytes = max(0, int(window.reserved_bytes) - int(reservation.reserved_bytes))
    window.attempted_bytes += actual
    reservation.actual_attempted_bytes = actual
    reservation.status = UploadQuotaReservationStatus.SETTLED
    reservation.settled_at = current
    await db.commit()


async def release_upload_slot(
    db: AsyncSession,
    batch: UploadBatch,
    *,
    now: datetime | None = None,
) -> None:
    """Release the admitted concurrent slot once, including crash recovery."""

    current = _aware_now(now)
    if batch.slot_released_at is not None:
        return
    result = await db.execute(
        select(UploadQuotaState)
        .where(UploadQuotaState.user_id == batch.owner_user_id)
        .with_for_update()
    )
    state = result.scalar_one()
    if state.active_slots <= 0:
        raise RuntimeError("upload slot counter would become negative")
    state.active_slots -= 1
    state.version += 1
    batch.slot_released_at = current
    reservations_result = await db.execute(
        select(UploadQuotaReservation)
        .where(UploadQuotaReservation.batch_id == batch.id)
        .with_for_update()
    )
    for reservation in reservations_result.scalars().all():
        if reservation.slot_released_at is None:
            reservation.slot_released_at = current
        if reservation.status == UploadQuotaReservationStatus.SETTLED:
            reservation.status = UploadQuotaReservationStatus.RELEASED
    await db.commit()


async def recover_stale_upload_batches(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Idempotently settle expired upload leases and release their durable slots."""

    current = _aware_now(now)
    batch_limit = max(1, min(500, int(limit)))
    try:
        result = await db.execute(
            select(UploadBatch)
            .where(
                UploadBatch.status == UploadBatchStatus.PENDING_UPLOAD,
                UploadBatch.slot_released_at.is_(None),
                UploadBatch.lease_expires_at <= current,
            )
            .order_by(UploadBatch.lease_expires_at.asc(), UploadBatch.id.asc())
            .limit(batch_limit)
            .with_for_update(skip_locked=True)
        )
        batches = result.scalars().all()

        for batch in batches:
            reservations_result = await db.execute(
                select(UploadQuotaReservation)
                .where(UploadQuotaReservation.batch_id == batch.id)
                .order_by(UploadQuotaReservation.part_ordinal.asc())
                .with_for_update()
            )
            reservations = reservations_result.scalars().all()
            for reservation in reservations:
                status = UploadQuotaReservationStatus(reservation.status)
                if status == UploadQuotaReservationStatus.RESERVED:
                    window_result = await db.execute(
                        select(UploadQuotaWindow)
                        .where(UploadQuotaWindow.id == reservation.quota_window_id)
                        .with_for_update()
                    )
                    window = window_result.scalar_one()
                    if int(window.reserved_bytes) < int(reservation.reserved_bytes):
                        raise RuntimeError("upload reserved-byte counter is inconsistent")
                    window.reserved_bytes = int(window.reserved_bytes) - int(
                        reservation.reserved_bytes
                    )
                    window.attempted_bytes = int(window.attempted_bytes) + int(
                        reservation.actual_attempted_bytes
                    )
                    reservation.settled_at = current
                if status in {
                    UploadQuotaReservationStatus.RESERVED,
                    UploadQuotaReservationStatus.SETTLED,
                }:
                    reservation.status = UploadQuotaReservationStatus.RELEASED
                    reservation.slot_released_at = current

            state_result = await db.execute(
                select(UploadQuotaState)
                .where(UploadQuotaState.user_id == batch.owner_user_id)
                .with_for_update()
            )
            state = state_result.scalar_one()
            if int(state.active_slots) <= 0:
                raise RuntimeError("upload slot counter would become negative")
            state.active_slots = int(state.active_slots) - 1
            state.version = int(state.version) + 1
            batch.status = UploadBatchStatus.UPLOAD_FAILED
            batch.failure_code = "upload_intent_expired"
            batch.slot_released_at = current

        await db.commit()
        return len(batches)
    except Exception:
        await db.rollback()
        raise
