"""Funding, entitlement, and retention invariants for private delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import uuid

from app.models.credit_grant_lot import GrantLotSourceType
from app.models.credit_reservation import ReservationStatus
from app.services.delivery_asset_service import (
    DeliverySettlementError,
    is_trial_funding_snapshot,
    retention_deadline_for_tier,
    validate_captured_entitlement_funding,
    validate_trial_unlock_grant,
)
from app.services.private_download_service import (
    PrivateDownloadError,
    _require_delivery_order_authority,
    validate_entitlement_download_authority,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _reservation(*, amount: int = 4):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        amount=amount,
        status=ReservationStatus.CAPTURED,
        captured_transaction_id=uuid.uuid4(),
        captured_retention_tier="paid_90d",
    )


class DeliverySettlementTest(unittest.TestCase):
    def test_trial_authority_comes_from_strict_funding_snapshot(self) -> None:
        self.assertTrue(
            is_trial_funding_snapshot(
                {"policy_version": "order-funding.v1", "is_trial": True, "allowed_lot_class": "WELCOME_ONLY"}
            )
        )
        self.assertFalse(
            is_trial_funding_snapshot(
                {"policy_version": "order-funding.v1", "is_trial": False, "allowed_lot_class": "PAID_ONLY"}
            )
        )
        for invalid in ({}, {"is_trial": True}, {"policy_version": "legacy", "is_trial": True, "allowed_lot_class": "WELCOME_ONLY"}):
            with self.subTest(invalid=invalid), self.assertRaises(DeliverySettlementError):
                is_trial_funding_snapshot(invalid)

    def test_entitlement_funding_exactly_reproduces_captured_allocations(self) -> None:
        reservation = _reservation()
        lots = [
            SimpleNamespace(
                id=uuid.uuid4(),
                user_id=reservation.user_id,
                root_transaction_id=uuid.uuid4(),
                source_type=GrantLotSourceType.PURCHASE,
                reversed_amount=0,
                frozen_amount=0,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                user_id=reservation.user_id,
                root_transaction_id=uuid.uuid4(),
                source_type=GrantLotSourceType.SUBSCRIPTION,
                reversed_amount=0,
                frozen_amount=0,
            ),
        ]
        allocations = [
            SimpleNamespace(id=uuid.uuid4(), reservation_id=reservation.id, grant_lot_id=lots[0].id, amount=1),
            SimpleNamespace(id=uuid.uuid4(), reservation_id=reservation.id, grant_lot_id=lots[1].id, amount=3),
        ]

        facts = validate_captured_entitlement_funding(
            reservation=reservation,
            allocations=allocations,
            lots=lots,
        )

        self.assertEqual(sum(item.amount for item in facts), reservation.amount)
        self.assertEqual(
            {item.root_transaction_id for item in facts},
            {lot.root_transaction_id for lot in lots},
        )
        lots[0].reversed_amount = 1
        with self.assertRaisesRegex(DeliverySettlementError, "delivery_funding_reversed_or_frozen"):
            validate_captured_entitlement_funding(
                reservation=reservation,
                allocations=allocations,
                lots=lots,
            )

    def test_retention_uses_original_ready_time_and_never_shrinks(self) -> None:
        original_ready_at = NOW - timedelta(days=10)
        current_expiry = NOW + timedelta(days=200)

        self.assertEqual(
            retention_deadline_for_tier(
                original_ready_at=original_ready_at,
                retention_tier="paid_90d",
                existing_expires_at=None,
            ),
            original_ready_at + timedelta(days=90),
        )
        self.assertEqual(
            retention_deadline_for_tier(
                original_ready_at=original_ready_at,
                retention_tier="subscription_180d",
                existing_expires_at=current_expiry,
            ),
            current_expiry,
        )

    def test_trial_unlock_requires_one_active_purchase_or_subscription_grant(self) -> None:
        lot = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            root_transaction_id=uuid.uuid4(),
            source_type=GrantLotSourceType.PURCHASE,
            original_amount=20,
            debt_offset_amount=0,
            reversed_amount=0,
            frozen_amount=0,
            consumed_amount=0,
            expires_at=NOW + timedelta(days=10),
            retention_tier="paid_90d",
        )

        validate_trial_unlock_grant(lot, user_id=lot.user_id, now=NOW)
        lot.source_type = GrantLotSourceType.ADMIN
        with self.assertRaisesRegex(DeliverySettlementError, "trial_unlock_grant_type_invalid"):
            validate_trial_unlock_grant(lot, user_id=lot.user_id, now=NOW)

    def test_download_requires_active_entitlement_and_exact_funding_rows(self) -> None:
        reservation = _reservation(amount=2)
        lot = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=reservation.user_id,
            root_transaction_id=uuid.uuid4(),
            source_type=GrantLotSourceType.PURCHASE,
            reversed_amount=0,
            frozen_amount=0,
        )
        allocation = SimpleNamespace(
            id=uuid.uuid4(), reservation_id=reservation.id, grant_lot_id=lot.id, amount=2
        )
        entitlement = SimpleNamespace(
            id=uuid.uuid4(),
            order_id=reservation.order_id,
            user_id=reservation.user_id,
            reservation_id=reservation.id,
            status="ACTIVE",
            expires_at=NOW + timedelta(days=2),
            unlock_grant_lot_id=None,
            unlock_root_transaction_id=None,
        )
        funding = SimpleNamespace(
            entitlement_id=entitlement.id,
            reservation_allocation_id=allocation.id,
            grant_lot_id=lot.id,
            amount=2,
        )

        validate_entitlement_download_authority(
            entitlement=entitlement,
            reservation=reservation,
            allocations=[allocation],
            funding_rows=[funding],
            lots=[lot],
            unlock_lot=None,
            order_id=reservation.order_id,
            user_id=reservation.user_id,
            now=NOW,
        )
        entitlement.status = "REVOKED"
        with self.assertRaisesRegex(Exception, "private_download_entitlement_inactive"):
            validate_entitlement_download_authority(
                entitlement=entitlement,
                reservation=reservation,
                allocations=[allocation],
                funding_rows=[funding],
                lots=[lot],
                unlock_lot=None,
                order_id=reservation.order_id,
                user_id=reservation.user_id,
                now=NOW,
            )

    def test_root_reversal_revokes_trial_unlock_binding_too(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app/services/credit_reversal_service.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OrderEntitlement.unlock_grant_lot_id == lot.id", source)


class PartnerDeliveryAuthorityTest(unittest.IsolatedAsyncioTestCase):
    def _order(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            generation_job_id=uuid.uuid4(),
            product_policy_snapshot={"policy_version": "order-policy.v1", "generation_mode": "couple"},
        )

    async def test_local_order_without_partner_lineage_remains_allowed(self) -> None:
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))

        await _require_delivery_order_authority(db, self._order())

        db.scalar.assert_awaited_once()

    async def test_completed_partner_lineage_without_withdrawal_is_allowed(self) -> None:
        order = self._order()
        invite = SimpleNamespace(
            id=uuid.uuid4(),
            host_user_id=order.user_id,
            partner_user_id=uuid.uuid4(),
            partner_asset_id=uuid.uuid4(),
            consent_event_id=uuid.uuid4(),
            order_id=order.id,
            job_id=order.generation_job_id,
            status="COMPLETED",
        )
        db = SimpleNamespace(scalar=AsyncMock(side_effect=[invite, None]))

        await _require_delivery_order_authority(db, order)

        self.assertEqual(db.scalar.await_count, 2)

    async def test_withdrawal_case_or_invalid_partner_lineage_denies_future_download(self) -> None:
        order = self._order()
        valid = SimpleNamespace(
            id=uuid.uuid4(),
            host_user_id=order.user_id,
            partner_user_id=uuid.uuid4(),
            partner_asset_id=uuid.uuid4(),
            consent_event_id=uuid.uuid4(),
            order_id=order.id,
            job_id=order.generation_job_id,
            status="COMPLETED",
        )
        for invite, case in (
            (valid, SimpleNamespace(status="CANCELLED_AND_DELETED")),
            (SimpleNamespace(**{**valid.__dict__, "status": "REVOKED"}), None),
        ):
            db = SimpleNamespace(scalar=AsyncMock(side_effect=[invite, case]))
            with self.subTest(status=invite.status, case=case), self.assertRaises(PrivateDownloadError) as raised:
                await _require_delivery_order_authority(db, order)
            self.assertEqual(raised.exception.code, "private_download_partner_consent_invalid")


if __name__ == "__main__":
    unittest.main()
