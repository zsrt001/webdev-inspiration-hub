"""Durable private-media deletion state-machine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.generation_attempt import GenerationAttemptStatus
from app.models.generation_job import GenerationJobStatus
from app.services.storage import DeleteResult


NOW = datetime(2026, 7, 13, 18, 30, tzinfo=timezone.utc)


def _asset(*, status=MediaAssetStatus.ACTIVE) -> MediaAsset:
    return MediaAsset(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        role=MediaAssetRole.SOURCE,
        storage_provider="s3",
        object_key="private/source.jpg",
        sha256="d" * 64,
        mime_type="image/jpeg",
        byte_size=10,
        width=10,
        height=10,
        access_level="private",
        policy_version="source-v1",
        expires_at=NOW + timedelta(days=1),
        status=status,
    )


class _Result:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _Db:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected query")
        return self.results.pop(0)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _ScalarDb:
    def __init__(self, results) -> None:
        self.results = list(results)

    async def scalars(self, _statement):
        if not self.results:
            raise AssertionError("unexpected scalar query")
        return _Result(self.results.pop(0))


class MediaDeletionTest(unittest.IsolatedAsyncioTestCase):
    def test_partner_case_open_blocks_and_pending_authorizes_only_owned_asset(self) -> None:
        from app.services.media_deletion_service import partner_case_asset_deletion_authority

        owned_id = uuid.uuid4()
        other_id = uuid.uuid4()
        case = SimpleNamespace(status="OPEN", owned_asset_ids=[str(owned_id)])
        blocker, waive_references = partner_case_asset_deletion_authority(case, owned_id)
        self.assertEqual(blocker, "partner_consent_case_open")
        self.assertFalse(waive_references)

        case.status = "SETTLED_DELETION_PENDING"
        blocker, waive_references = partner_case_asset_deletion_authority(case, owned_id)
        self.assertIsNone(blocker)
        self.assertTrue(waive_references)
        blocker, waive_references = partner_case_asset_deletion_authority(case, other_id)
        self.assertEqual(blocker, "partner_consent_case_asset_not_owned")
        self.assertFalse(waive_references)

    def test_partner_case_closes_only_after_every_owned_asset_is_deleted(self) -> None:
        from app.services.media_deletion_service import partner_case_can_close

        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        case = SimpleNamespace(
            status="SETTLED_DELETION_PENDING",
            owned_asset_ids=[str(first_id), str(second_id)],
        )
        assets = [
            SimpleNamespace(id=first_id, status=MediaAssetStatus.DELETED),
            SimpleNamespace(id=second_id, status=MediaAssetStatus.DELETE_FAILED),
        ]
        self.assertFalse(partner_case_can_close(case, assets))
        assets[1].status = MediaAssetStatus.DELETED
        self.assertTrue(partner_case_can_close(case, assets))
        self.assertFalse(partner_case_can_close(case, assets[:1]))

    async def test_last_verified_object_deletion_closes_partner_case(self) -> None:
        from app.services.media_deletion_service import _close_settled_partner_cases_for_asset

        first = _asset(status=MediaAssetStatus.DELETED)
        second = _asset(status=MediaAssetStatus.DELETED)
        case = SimpleNamespace(
            status="SETTLED_DELETION_PENDING",
            owned_asset_ids=[str(first.id), str(second.id)],
            version=2,
            closed_at=None,
        )
        db = _ScalarDb([[case], [first, second]])

        closed = await _close_settled_partner_cases_for_asset(
            db,
            asset_id=second.id,
            now=NOW,
        )

        self.assertEqual(closed, 1)
        self.assertEqual(case.status, "CANCELLED_AND_DELETED")
        self.assertEqual(case.version, 3)
        self.assertEqual(case.closed_at, NOW)

    def test_generation_reference_releases_only_after_terminal_settlement(self) -> None:
        from app.services.media_deletion_service import generation_reference_blocker

        blocking_cases = (
            (GenerationJobStatus.QUEUED, [], "RESERVED", "PENDING"),
            (GenerationJobStatus.ACTIVE, [], "CAPTURED", "PUBLISHED"),
            (
                GenerationJobStatus.FINISHED,
                [GenerationAttemptStatus.SUBMITTED],
                "CAPTURED",
                "PUBLISHED",
            ),
            (GenerationJobStatus.FAILED, [], "RESERVED", "PENDING"),
            (GenerationJobStatus.FINISHED, [], "CAPTURED", "PENDING"),
        )
        for status, attempts, settlement, delivery in blocking_cases:
            with self.subTest(status=status, attempts=attempts, settlement=settlement, delivery=delivery):
                self.assertEqual(
                    generation_reference_blocker(
                        job_status=status,
                        attempt_statuses=attempts,
                        settlement_status=settlement,
                        delivery_status=delivery,
                    ),
                    "generation_reference_unresolved",
                )

        self.assertIsNone(
            generation_reference_blocker(
                job_status=GenerationJobStatus.FINISHED,
                attempt_statuses=[GenerationAttemptStatus.FINISHED],
                settlement_status="CAPTURED",
                delivery_status="PUBLISHED",
            )
        )
        self.assertIsNone(
            generation_reference_blocker(
                job_status=GenerationJobStatus.FAILED,
                attempt_statuses=[GenerationAttemptStatus.FAILED],
                settlement_status="REFUNDED",
                delivery_status="NOT_DELIVERED",
            )
        )
        self.assertIsNone(
            generation_reference_blocker(
                job_status=GenerationJobStatus.FINISHED,
                attempt_statuses=[GenerationAttemptStatus.FAILED],
                settlement_status="REFUNDED",
                delivery_status="REVOKED",
            )
        )

    async def test_active_reference_revokes_read_but_defers_physical_delete(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset()
        db = _Db()
        with (
            patch.object(service, "_lock_asset", AsyncMock(return_value=asset)),
            patch.object(
                service,
                "run_reference_guard",
                AsyncMock(return_value=["active_provider_grant"]),
            ),
        ):
            result = await service.request_asset_deletion(
                db,
                asset.id,
                reason="retention_expired",
                now=NOW,
            )

        self.assertEqual(result.code, "active_reference")
        self.assertEqual(result.blockers, ("active_provider_grant",))
        self.assertEqual(asset.status, MediaAssetStatus.PENDING_DELETE)
        self.assertEqual(asset.read_revoked_at, NOW)
        self.assertEqual(asset.deletion_reason, "retention_expired")
        self.assertEqual(asset.deletion_blockers, ["active_provider_grant"])
        self.assertGreater(asset.next_delete_at, NOW)
        self.assertEqual(asset.object_key, "private/source.jpg")
        self.assertEqual(db.commits, 1)

    async def test_expired_lease_is_reclaimed_with_higher_fence(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset(status=MediaAssetStatus.PENDING_DELETE)
        asset.deletion_reason = "retention_expired"
        asset.deletion_blockers = []
        asset.next_delete_at = NOW
        asset.lease_owner = "dead-cleaner"
        asset.lease_claim_id = uuid.uuid4()
        asset.lease_expires_at = NOW - timedelta(seconds=1)
        asset.fencing_token = 7
        db = _Db([_Result([asset])])

        claims = await service.claim_deletion_batch(
            db,
            lease_owner="cleaner-b",
            now=NOW,
            limit=10,
        )

        self.assertEqual(len(claims), 1)
        self.assertEqual(asset.lease_owner, "cleaner-b")
        self.assertEqual(asset.fencing_token, 8)
        self.assertGreater(asset.lease_expires_at, NOW)
        self.assertEqual(db.commits, 1)

    async def test_delete_failure_preserves_key_and_stale_claim_cannot_overwrite(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset(status=MediaAssetStatus.PENDING_DELETE)
        asset.deletion_reason = "user_request"
        asset.deletion_blockers = []
        asset.lease_owner = "cleaner"
        asset.lease_claim_id = uuid.uuid4()
        asset.lease_expires_at = NOW + timedelta(seconds=120)
        asset.fencing_token = 3
        db = _Db()
        with (
            patch.object(service, "_lock_asset", AsyncMock(return_value=asset)),
            patch.object(
                service,
                "_close_settled_partner_cases_for_asset",
                AsyncMock(return_value=0),
            ),
        ):
            await service.confirm_storage_deletion(
                db,
                asset_id=asset.id,
                lease_claim_id=asset.lease_claim_id,
                fencing_token=3,
                result=DeleteResult.FAILED,
                now=NOW,
            )

        self.assertEqual(asset.status, MediaAssetStatus.DELETE_FAILED)
        self.assertEqual(asset.object_key, "private/source.jpg")
        self.assertEqual(asset.delete_attempts, 1)
        self.assertGreater(asset.next_delete_at, NOW)
        self.assertIsNone(asset.lease_claim_id)

        with (
            patch.object(service, "_lock_asset", AsyncMock(return_value=asset)),
            self.assertRaises(service.DeletionClaimError),
        ):
            await service.confirm_storage_deletion(
                _Db(),
                asset_id=asset.id,
                lease_claim_id=uuid.uuid4(),
                fencing_token=3,
                result=DeleteResult.DELETED,
                now=NOW,
            )
        self.assertEqual(asset.status, MediaAssetStatus.DELETE_FAILED)

    async def test_storage_not_found_settles_deleted_once(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset(status=MediaAssetStatus.PENDING_DELETE)
        asset.deletion_reason = "retention_expired"
        asset.deletion_blockers = []
        claim_id = uuid.uuid4()
        asset.lease_owner = "cleaner"
        asset.lease_claim_id = claim_id
        asset.lease_expires_at = NOW + timedelta(seconds=120)
        asset.fencing_token = 2
        db = _Db()
        with (
            patch.object(service, "_lock_asset", AsyncMock(return_value=asset)),
            patch.object(
                service,
                "_close_settled_partner_cases_for_asset",
                AsyncMock(return_value=0),
            ),
        ):
            await service.confirm_storage_deletion(
                db,
                asset_id=asset.id,
                lease_claim_id=claim_id,
                fencing_token=2,
                result=DeleteResult.NOT_FOUND,
                now=NOW,
            )

        self.assertEqual(asset.status, MediaAssetStatus.DELETED)
        self.assertEqual(asset.deleted_at, NOW)
        self.assertEqual(asset.object_key, "private/source.jpg")
        self.assertIsNone(asset.lease_owner)
        self.assertEqual(db.commits, 1)

    async def test_blockers_are_rechecked_and_cleared_before_claim(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset(status=MediaAssetStatus.PENDING_DELETE)
        asset.deletion_reason = "retention_expired"
        asset.deletion_blockers = ["active_provider_grant"]
        asset.next_delete_at = NOW
        db = _Db([_Result([asset])])
        with patch.object(service, "run_reference_guard", AsyncMock(return_value=[])):
            refreshed = await service.recheck_blocked_deletions(
                db,
                now=NOW,
                limit=10,
            )

        self.assertEqual(refreshed, 1)
        self.assertEqual(asset.deletion_blockers, [])
        self.assertEqual(asset.next_delete_at, NOW)
        self.assertEqual(db.commits, 1)

    async def test_storage_exception_becomes_retryable_failed_result(self) -> None:
        from app.services import media_deletion_service as service

        asset = _asset(status=MediaAssetStatus.PENDING_DELETE)
        asset.deletion_reason = "retention_expired"
        asset.deletion_blockers = []
        asset.lease_owner = "cleaner"
        asset.lease_claim_id = uuid.uuid4()
        asset.lease_expires_at = NOW + timedelta(seconds=120)
        asset.fencing_token = 4

        class FailingStore:
            def delete_private(self, _object_key: str):
                raise OSError("private storage unavailable")

        confirmation = AsyncMock()
        with patch.object(service, "confirm_storage_deletion", confirmation):
            result = await service.run_claimed_deletion(
                _Db(),
                asset,
                object_store=FailingStore(),
                now=NOW,
            )

        self.assertEqual(result, DeleteResult.FAILED)
        self.assertEqual(confirmation.await_args.kwargs["result"], DeleteResult.FAILED)


if __name__ == "__main__":
    unittest.main()
