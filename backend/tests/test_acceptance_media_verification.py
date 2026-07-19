"""Physical private-object absence must be proven after acceptance cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import uuid

from app.services.acceptance_media_verification_service import (
    AcceptanceMediaVerificationError,
    verify_acceptance_media_absence,
)


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalar_one_or_none(self):
        return self.values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.values))


class _Store:
    def __init__(self, *, readable: bool = False, unavailable: bool = False):
        self.readable = readable
        self.unavailable = unavailable
        self.keys: list[str] = []

    def read_private(self, object_key: str) -> bytes:
        self.keys.append(object_key)
        if self.unavailable:
            raise RuntimeError("private store unavailable")
        if self.readable:
            return b"still-present"
        raise FileNotFoundError("private object not found")


def _asset(index: int):
    return SimpleNamespace(
        id=uuid.UUID(int=index + 1),
        status="DELETED",
        read_revoked_at=NOW,
        deleted_at=NOW + timedelta(seconds=5),
        object_key=f"private/user/source-{index}.jpg",
        sha256=f"{index + 1:064x}",
    )


class AcceptanceMediaVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_requires_read_after_delete_not_found_for_every_asset(self) -> None:
        tombstone = SimpleNamespace(
            media_cleanup_pending=False,
            closed_at=NOW,
        )
        assets = [_asset(0), _asset(1)]
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _ScalarResult(tombstone),
                    _ScalarResult(assets),
                ]
            )
        )
        store = _Store()
        proof = await verify_acceptance_media_absence(
            db,
            user_id=uuid.uuid4(),
            object_store=store,
        )
        self.assertEqual(proof.verified_asset_count, 2)
        self.assertEqual(len(proof.facts_sha256), 64)
        self.assertEqual(store.keys, [asset.object_key for asset in assets])

    async def test_rejects_readable_or_unverifiable_private_objects(self) -> None:
        for store in (_Store(readable=True), _Store(unavailable=True)):
            with self.subTest(store=store):
                db = SimpleNamespace(
                    execute=AsyncMock(
                        side_effect=[
                            _ScalarResult(
                                SimpleNamespace(
                                    media_cleanup_pending=False,
                                    closed_at=NOW,
                                )
                            ),
                            _ScalarResult([_asset(0)]),
                        ]
                    )
                )
                with self.assertRaises(AcceptanceMediaVerificationError):
                    await verify_acceptance_media_absence(
                        db,
                        user_id=uuid.uuid4(),
                        object_store=store,
                    )

    async def test_rejects_incomplete_database_cleanup(self) -> None:
        asset = _asset(0)
        asset.status = "PENDING_DELETE"
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _ScalarResult(
                        SimpleNamespace(
                            media_cleanup_pending=False,
                            closed_at=NOW,
                        )
                    ),
                    _ScalarResult([asset]),
                ]
            )
        )
        with self.assertRaisesRegex(
            AcceptanceMediaVerificationError,
            "not fully deleted",
        ):
            await verify_acceptance_media_absence(
                db,
                user_id=uuid.uuid4(),
                object_store=_Store(),
            )


if __name__ == "__main__":
    unittest.main()
