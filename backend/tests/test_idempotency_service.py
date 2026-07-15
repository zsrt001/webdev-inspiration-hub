"""Request-hash idempotency behavior."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid

from app.models.idempotency_record import IdempotencyRecord, IdempotencyState
from app.services.idempotency_service import (
    IdempotencyConflict,
    begin_idempotent_request,
    complete_idempotent_request,
)


class _Db:
    def __init__(self) -> None:
        self.record: IdempotencyRecord | None = None

    async def execute(self, *_args, **_kwargs):
        return None

    async def scalar(self, _statement):
        return self.record

    def add(self, value):
        self.record = value

    async def flush(self):
        return None


class IdempotencyServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_scope_key_and_hash_reuses_stored_result(self) -> None:
        db = _Db()
        user_id = uuid.uuid4()
        first = await begin_idempotent_request(
            db,
            user_id=user_id,
            endpoint="orders.create",
            key="key-1",
            request_hash="hash-a",
        )
        second = await begin_idempotent_request(
            db,
            user_id=user_id,
            endpoint="orders.create",
            key="key-1",
            request_hash="hash-a",
        )
        self.assertEqual(first.record_id, second.record_id)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)

    async def test_same_key_with_different_hash_conflicts(self) -> None:
        db = _Db()
        user_id = uuid.uuid4()
        await begin_idempotent_request(
            db,
            user_id=user_id,
            endpoint="orders.create",
            key="key-1",
            request_hash="hash-a",
        )
        with self.assertRaises(IdempotencyConflict):
            await begin_idempotent_request(
                db,
                user_id=user_id,
                endpoint="orders.create",
                key="key-1",
                request_hash="hash-b",
            )

    async def test_completion_is_exactly_once_and_replay_exposes_response(self) -> None:
        db = _Db()
        attempt = await begin_idempotent_request(
            db,
            user_id=uuid.uuid4(),
            endpoint="admin.credit.adjust",
            key="request-1",
            request_hash="payload-hash",
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        await complete_idempotent_request(
            db,
            record_id=attempt.record_id,
            response_status=200,
            response_json={"ok": True},
        )
        self.assertEqual(db.record.state, IdempotencyState.COMPLETED)
        replay = await begin_idempotent_request(
            db,
            user_id=db.record.user_id,
            endpoint="admin.credit.adjust",
            key="request-1",
            request_hash="payload-hash",
        )
        self.assertEqual(replay.response_json, {"ok": True})


if __name__ == "__main__":
    unittest.main()
