"""Crash-safe, IDs-only generation outbox dispatch contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
import uuid

from sqlalchemy.dialects import postgresql


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.services.outbox_service import (
    GenerationOutboxContractError,
    generation_message_from_event,
    publish_pending_generation_outbox,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _event(*, payload: dict | None = None) -> OutboxEvent:
    job_id = uuid.uuid4()
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="generation_job",
        aggregate_id=job_id,
        event_type="GENERATION_JOB_CREATED",
        dedupe_key=f"generation:v1:{job_id}",
        payload_version="generation-job.v1",
        payload_json=payload or {
            "job_id": str(job_id),
            "payload_version": "generation-job.v1",
        },
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=NOW,
        fencing_token=0,
    )


class _ScalarRows:
    def __init__(self, rows: list[OutboxEvent]):
        self._rows = rows

    def all(self) -> list[OutboxEvent]:
        return self._rows


class _OutboxDb:
    def __init__(self, rows: list[OutboxEvent]):
        self.rows = rows
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return _ScalarRows(self.rows)


class _Redis:
    def __init__(self, *, result=object(), error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    async def enqueue_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class OutboxDispatchTest(unittest.IsolatedAsyncioTestCase):
    def test_message_is_ids_only_and_deterministically_named(self) -> None:
        event = _event()

        message = generation_message_from_event(event)

        self.assertEqual(message.function_name, "generate_order_v1")
        self.assertEqual(message.job_id, event.aggregate_id)
        self.assertEqual(message.redis_job_id, f"generation:v1:{event.aggregate_id}")
        self.assertEqual(message.payload_version, "generation-job.v1")

    def test_old_or_expanded_payload_is_rejected(self) -> None:
        event = _event(payload={"job_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4())})

        with self.assertRaises(GenerationOutboxContractError):
            generation_message_from_event(event)

    async def test_publish_uses_skip_locked_and_redis_dedupe(self) -> None:
        event = _event()
        db = _OutboxDb([event])
        redis = _Redis(result=None)  # None is ARQ's duplicate-job response.

        result = await publish_pending_generation_outbox(db, redis, limit=10, now=NOW)

        self.assertEqual(result.dispatched_event_ids, (event.id,))
        self.assertEqual(event.status, OutboxEventStatus.DISPATCHED)
        self.assertEqual(event.dispatched_at, NOW)
        self.assertEqual(
            redis.calls,
            [
                (
                    ("generate_order_v1", str(event.aggregate_id), "generation-job.v1"),
                    {"_job_id": f"generation:v1:{event.aggregate_id}"},
                )
            ],
        )
        sql = str(db.statement.compile(dialect=postgresql.dialect())).upper()
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("SKIP LOCKED", sql)

    async def test_third_infrastructure_failure_dead_letters_once(self) -> None:
        event = _event()
        event.attempt_count = 2
        db = _OutboxDb([event])
        redis = _Redis(error=OSError("redis unavailable: secret must not leak"))

        first = await publish_pending_generation_outbox(db, redis, limit=10, now=NOW)
        second = await publish_pending_generation_outbox(db, redis, limit=10, now=NOW)

        self.assertEqual(first.failed_event_ids, (event.id,))
        self.assertEqual(second.failed_event_ids, ())
        self.assertEqual(event.status, OutboxEventStatus.FAILED)
        self.assertEqual(event.attempt_count, 3)
        self.assertEqual(event.last_error, "OSError")


if __name__ == "__main__":
    unittest.main()
