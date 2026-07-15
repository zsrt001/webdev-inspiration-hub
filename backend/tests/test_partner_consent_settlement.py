"""Consent withdrawal decisions stay fail-closed across generation states."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock
from unittest.mock import patch

from app.models.generation_attempt import GenerationAttemptStatus
from app.models.generation_job import GenerationJobStatus
from app.models.media_asset import MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.services.partner_invite_service import (
    _stage_case_asset,
    plan_partner_withdrawal,
    settle_open_partner_consent_case_after_provider,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class PartnerConsentSettlementTest(unittest.TestCase):
    def test_queued_order_can_enter_consent_review(self) -> None:
        order = Order(status=OrderStatus.QUEUED)

        self.assertTrue(order.can_transition_to(OrderStatus.CONSENT_REVIEW_REQUIRED))

    def test_pre_order_withdrawal_deletes_partner_source_without_creating_order_case(self) -> None:
        plan = plan_partner_withdrawal(
            order_status=None,
            job_status=None,
            settlement_status=None,
            successful_final_downloads=0,
        )
        self.assertFalse(plan.requires_case)
        self.assertTrue(plan.delete_partner_source)
        self.assertFalse(plan.refund_captured)

    def test_queued_job_release_and_submitted_job_reconciliation_are_distinct(self) -> None:
        queued = plan_partner_withdrawal(
            order_status=OrderStatus.QUEUED,
            job_status=GenerationJobStatus.QUEUED,
            attempt_statuses=[],
            settlement_status="RESERVED",
            successful_final_downloads=0,
        )
        self.assertTrue(queued.requires_case)
        self.assertTrue(queued.release_reserved)
        self.assertFalse(queued.request_provider_cancel)

        submitted = plan_partner_withdrawal(
            order_status=OrderStatus.GENERATING,
            job_status=GenerationJobStatus.ACTIVE,
            attempt_statuses=[GenerationAttemptStatus.SUBMITTING],
            settlement_status="CAPTURED",
            successful_final_downloads=0,
        )
        self.assertTrue(submitted.requires_case)
        self.assertTrue(submitted.request_provider_cancel)
        self.assertFalse(submitted.refund_captured)

        prepared = plan_partner_withdrawal(
            order_status=OrderStatus.GENERATING,
            job_status=GenerationJobStatus.ACTIVE,
            attempt_statuses=[GenerationAttemptStatus.PREPARED],
            settlement_status="RESERVED",
            successful_final_downloads=0,
        )
        self.assertTrue(prepared.release_reserved)
        self.assertFalse(prepared.request_provider_cancel)

    def test_ready_refund_requires_proof_that_no_final_download_succeeded(self) -> None:
        clean = plan_partner_withdrawal(
            order_status=OrderStatus.READY,
            job_status=GenerationJobStatus.FINISHED,
            settlement_status="CAPTURED",
            successful_final_downloads=0,
        )
        downloaded = plan_partner_withdrawal(
            order_status=OrderStatus.READY,
            job_status=GenerationJobStatus.FINISHED,
            settlement_status="CAPTURED",
            successful_final_downloads=1,
        )

        self.assertTrue(clean.revoke_downloads)
        self.assertTrue(clean.delete_derived_assets)
        self.assertTrue(clean.refund_captured)
        self.assertFalse(downloaded.refund_captured)
        self.assertTrue(downloaded.revoke_downloads)


class PartnerConsentAssetStagingTest(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_provider_fact_refunds_once_and_releases_case_to_deletion(self) -> None:
        order_id = uuid.uuid4()
        job_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        reservation_id = uuid.uuid4()
        invite_id = uuid.uuid4()
        invite = SimpleNamespace(id=invite_id, job_id=job_id, status="COMPLETED")
        case = SimpleNamespace(
            id=uuid.uuid4(),
            invite_id=invite_id,
            order_id=order_id,
            job_id=job_id,
            status="OPEN",
            provider_cancel_required=True,
            settlement_transaction_id=None,
            settled_at=None,
            version=1,
        )
        order = SimpleNamespace(
            id=order_id,
            generation_job_id=job_id,
            reservation_id=reservation_id,
            status=OrderStatus.CONSENT_REVIEW_REQUIRED,
            settlement_status="CAPTURED",
            delivery_status="REVOKED",
        )
        job = SimpleNamespace(
            id=job_id,
            order_id=order_id,
            status=GenerationJobStatus.ACTIVE,
            settlement_status="CAPTURED",
            delivery_status="REVOKED",
            lease_owner="worker",
            lease_claim_id=uuid.uuid4(),
            lease_expires_at=NOW,
            heartbeat_at=NOW,
            finished_at=None,
        )
        attempt = SimpleNamespace(
            id=attempt_id,
            job_id=job_id,
            status=GenerationAttemptStatus.SUBMITTED,
            finished_at=None,
        )
        reservation = SimpleNamespace(id=reservation_id, status="CAPTURED")
        db = SimpleNamespace(
            scalar=AsyncMock(
                side_effect=[invite, case, order, job, attempt, reservation, 0]
            ),
            flush=AsyncMock(),
        )
        transaction = SimpleNamespace(id=uuid.uuid4())
        settlement = SimpleNamespace(transaction=transaction)

        with patch(
            "app.services.partner_invite_service.refund_captured_reservation",
            AsyncMock(return_value=settlement),
        ) as refund:
            settled = await settle_open_partner_consent_case_after_provider(
                db,
                job_id=job_id,
                attempt_id=attempt_id,
                provider_terminal_state="SUCCEEDED",
                now=NOW,
            )

        self.assertTrue(settled)
        refund.assert_awaited_once()
        self.assertEqual(case.status, "SETTLED_DELETION_PENDING")
        self.assertFalse(case.provider_cancel_required)
        self.assertEqual(case.settlement_transaction_id, transaction.id)
        self.assertEqual(order.settlement_status, "REFUNDED")
        self.assertEqual(job.status, GenerationJobStatus.CANCELLED)
        self.assertEqual(attempt.status, GenerationAttemptStatus.FAILED)
        db.flush.assert_awaited_once()

    async def test_pending_upload_is_flushed_through_upload_failed_before_delete(self) -> None:
        db = SimpleNamespace(flush=AsyncMock())
        asset = SimpleNamespace(
            status=MediaAssetStatus.PENDING_UPLOAD,
            read_revoked_at=None,
            deletion_reason=None,
            deletion_blockers=None,
            next_delete_at=None,
        )
        now = datetime(2026, 7, 14, tzinfo=timezone.utc)

        await _stage_case_asset(
            db,
            asset,
            reason="partner_consent_withdrawn",
            now=now,
        )

        db.flush.assert_awaited_once()
        self.assertEqual(asset.status, MediaAssetStatus.PENDING_DELETE)
        self.assertEqual(asset.read_revoked_at, now)

    async def test_active_asset_moves_directly_to_pending_delete_without_intermediate_flush(self) -> None:
        db = SimpleNamespace(flush=AsyncMock())
        asset = SimpleNamespace(
            status=MediaAssetStatus.ACTIVE,
            read_revoked_at=None,
            deletion_reason=None,
            deletion_blockers=None,
            next_delete_at=None,
        )

        await _stage_case_asset(
            db,
            asset,
            reason="partner_consent_withdrawn",
            now=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

        db.flush.assert_not_awaited()
        self.assertEqual(asset.status, MediaAssetStatus.PENDING_DELETE)


if __name__ == "__main__":
    unittest.main()
