"""Root reversal and commercial merge lineage contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.subscription_cancel_intent import CancelIntentState, SubscriptionCancelIntent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_invoice import SubscriptionInvoice
from app.models.user_credit import UserCredit
from app.models.user_subscription import NormalizedSubscriptionStatus, UserSubscription
from app.services.credit_reversal_service import (
    CreditReversalError,
    apply_root_reversal_counters,
)
from app.services.account_merge_credit_service import (
    AccountMergeCreditError,
    require_generation_graph_mergeable,
    rebind_terminal_partner_references,
    ordered_merge_user_ids,
    select_subscription_projection_for_merge,
)


class RootReversalTest(unittest.TestCase):
    def test_nonterminal_partner_consent_case_blocks_merge(self) -> None:
        canonical_id = uuid.uuid4()
        legacy_id = uuid.uuid4()
        invite = SimpleNamespace(
            status="COMPLETED",
            host_user_id=legacy_id,
            partner_user_id=uuid.uuid4(),
            version=4,
        )
        for status in ("OPEN", "SETTLED_DELETION_PENDING"):
            case = SimpleNamespace(
                status=status,
                host_user_id=legacy_id,
                partner_user_id=invite.partner_user_id,
                version=1,
            )
            with self.subTest(status=status), self.assertRaises(AccountMergeCreditError) as raised:
                rebind_terminal_partner_references(
                    canonical_user_id=canonical_id,
                    legacy_user_id=legacy_id,
                    invites=[invite],
                    consent_cases=[case],
                )
            self.assertEqual(raised.exception.code, "partner_consent_case_nonterminal")

    def test_only_terminal_fully_settled_partner_references_are_rebound(self) -> None:
        canonical_id = uuid.uuid4()
        legacy_id = uuid.uuid4()
        partner_id = uuid.uuid4()
        invite = SimpleNamespace(
            status="COMPLETED",
            host_user_id=legacy_id,
            partner_user_id=partner_id,
            version=4,
        )
        case = SimpleNamespace(
            status="CANCELLED_AND_DELETED",
            host_user_id=legacy_id,
            partner_user_id=partner_id,
            version=2,
        )

        rebind_terminal_partner_references(
            canonical_user_id=canonical_id,
            legacy_user_id=legacy_id,
            invites=[invite],
            consent_cases=[case],
        )

        self.assertEqual(invite.host_user_id, canonical_id)
        self.assertEqual(invite.version, 5)
        self.assertEqual(case.host_user_id, canonical_id)
        self.assertEqual(case.version, 3)

    def test_active_invite_and_participant_collision_each_block_merge(self) -> None:
        canonical_id = uuid.uuid4()
        legacy_id = uuid.uuid4()
        for invite, code in (
            (
                SimpleNamespace(
                    status="CONSENTED",
                    host_user_id=legacy_id,
                    partner_user_id=uuid.uuid4(),
                    version=3,
                ),
                "partner_invite_nonterminal",
            ),
            (
                SimpleNamespace(
                    status="COMPLETED",
                    host_user_id=legacy_id,
                    partner_user_id=canonical_id,
                    version=4,
                ),
                "partner_invite_identity_collision",
            ),
        ):
            with self.subTest(code=code), self.assertRaises(AccountMergeCreditError) as raised:
                rebind_terminal_partner_references(
                    canonical_user_id=canonical_id,
                    legacy_user_id=legacy_id,
                    invites=[invite],
                    consent_cases=[],
                )
            self.assertEqual(raised.exception.code, code)

    def test_consumed_reversal_creates_debt_and_never_restores_spendable(self) -> None:
        user_id = uuid.uuid4()
        lot = CreditGrantLot(
            id=uuid.uuid4(),
            user_id=user_id,
            root_transaction_id=uuid.uuid4(),
            source_type=GrantLotSourceType.PURCHASE,
            source_id="purchase-1",
            original_amount=50,
            debt_offset_amount=0,
            reversed_amount=0,
            frozen_amount=0,
            consumed_amount=40,
            retention_tier="paid_90d",
        )
        credit = UserCredit(user_id=user_id, balance=10, reserved_balance=0)

        debt = apply_root_reversal_counters(lot, credit, amount=50)

        self.assertEqual(lot.reversed_amount, 50)
        self.assertEqual(lot.spendable_amount, 0)
        self.assertEqual(credit.balance, -40)
        self.assertEqual(credit.spendable_balance, 0)
        self.assertEqual(debt, 40)

    def test_cumulative_reversal_cannot_exceed_root(self) -> None:
        user_id = uuid.uuid4()
        lot = CreditGrantLot(
            id=uuid.uuid4(),
            user_id=user_id,
            root_transaction_id=uuid.uuid4(),
            source_type=GrantLotSourceType.PURCHASE,
            source_id="purchase-1",
            original_amount=50,
            debt_offset_amount=0,
            reversed_amount=45,
            frozen_amount=0,
            consumed_amount=0,
            retention_tier="paid_90d",
        )
        credit = UserCredit(user_id=user_id, balance=5, reserved_balance=0)
        with self.assertRaises(CreditReversalError):
            apply_root_reversal_counters(lot, credit, amount=6)

    def test_merge_lock_order_is_stable_uuid_order(self) -> None:
        high = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
        low = uuid.UUID("00000000-0000-4000-8000-000000000001")
        self.assertEqual(ordered_merge_user_ids(high, low), (low, high))
        self.assertEqual(ordered_merge_user_ids(low, high), (low, high))

    def test_nonterminal_or_unsettled_generation_graph_blocks_merge(self) -> None:
        from types import SimpleNamespace

        terminal_job = SimpleNamespace(
            id=uuid.uuid4(),
            status="FINISHED",
            lease_owner=None,
            lease_claim_id=None,
            lease_expires_at=None,
            settlement_status="CAPTURED",
            delivery_status="PUBLISHED",
        )
        require_generation_graph_mergeable(
            jobs=[terminal_job],
            attempts=[SimpleNamespace(status="FINISHED")],
            reservations=[SimpleNamespace(status="CAPTURED", provider_attempt_id=uuid.uuid4())],
            qa_verdicts=[SimpleNamespace(job_id=terminal_job.id, decision="PASS")],
        )
        withdrawn_job = SimpleNamespace(
            **{
                **terminal_job.__dict__,
                "settlement_status": "REFUNDED",
                "delivery_status": "REVOKED",
            }
        )
        require_generation_graph_mergeable(
            jobs=[withdrawn_job],
            attempts=[SimpleNamespace(status="FAILED")],
            reservations=[
                SimpleNamespace(status="CAPTURED", provider_attempt_id=uuid.uuid4())
            ],
        )

        invalid = (
            ([SimpleNamespace(**{**terminal_job.__dict__, "status": "ACTIVE"})], [], [], "nonterminal_generation_job"),
            ([terminal_job], [SimpleNamespace(status="UNKNOWN")], [], "nonterminal_generation_attempt"),
            (
                [SimpleNamespace(**{**terminal_job.__dict__, "lease_owner": "worker"})],
                [],
                [],
                "generation_lease_unsettled",
            ),
            (
                [terminal_job],
                [SimpleNamespace(status="FINISHED")],
                [SimpleNamespace(status="RESERVED", provider_attempt_id=None)],
                "nonterminal_reservation_exists",
            ),
            (
                [SimpleNamespace(**{**terminal_job.__dict__, "delivery_status": "PENDING"})],
                [SimpleNamespace(status="FINISHED")],
                [SimpleNamespace(status="CAPTURED", provider_attempt_id=uuid.uuid4())],
                "generation_settlement_incomplete",
            ),
        )
        for jobs, attempts, reservations, code in invalid:
            with self.subTest(code=code), self.assertRaises(AccountMergeCreditError) as raised:
                require_generation_graph_mergeable(
                    jobs=jobs,
                    attempts=attempts,
                    reservations=reservations,
                )
            self.assertEqual(raised.exception.code, code)


class SubscriptionMergeLineageTest(unittest.TestCase):
    def _normalized_lineage(self):
        canonical_id = uuid.uuid4()
        legacy_id = uuid.uuid4()
        subscription = UserSubscription(
            id=uuid.uuid4(),
            user_id=legacy_id,
            plan_id=uuid.uuid4(),
            provider="creem",
            provider_subscription_id="sub_merge_1",
            normalized_status=NormalizedSubscriptionStatus.ACTIVE,
            product_code="starter_monthly",
            catalog_version_id=uuid.uuid4(),
            catalog_snapshot={"credits": 80},
        )
        now = datetime(2026, 7, 13, tzinfo=timezone.utc)
        invoice = SubscriptionInvoice(
            id=uuid.uuid4(),
            subscription_id=subscription.id,
            user_id=legacy_id,
            payment_event_id=uuid.uuid4(),
            provider="creem",
            provider_transaction_id="tran_merge_1",
            period_start=now,
            period_end=datetime(2026, 8, 13, tzinfo=timezone.utc),
            pre_tax_minor_units=1900,
            tax_minor_units=152,
            currency="USD",
            provider_status="paid",
            occurred_at=now,
            raw_payload_sha256="a" * 64,
            catalog_version_id=subscription.catalog_version_id,
            catalog_snapshot={"credits": 80},
            refunded_minor_units=0,
            disputed_minor_units=0,
            dispute_state="NONE",
        )
        grant = SubscriptionCreditGrant(
            id=uuid.uuid4(),
            subscription_id=subscription.id,
            user_id=legacy_id,
            period_key=now.isoformat(),
            period_start=now,
            period_end=invoice.period_end,
            credits=80,
            invoice_id=invoice.id,
            credit_transaction_id=uuid.uuid4(),
            grant_lot_id=uuid.uuid4(),
        )
        invoice.credit_grant_id = grant.id
        return canonical_id, legacy_id, subscription, invoice, grant

    def test_single_normalized_legacy_projection_is_the_only_mutable_subscription_row(self) -> None:
        canonical_id, legacy_id, subscription, invoice, grant = self._normalized_lineage()
        selected = select_subscription_projection_for_merge(
            canonical_user_id=canonical_id,
            legacy_user_id=legacy_id,
            subscriptions=[subscription],
            invoices=[invoice],
            grants=[grant],
            cancel_intents=[],
        )
        self.assertIs(selected, subscription)
        self.assertEqual(invoice.user_id, legacy_id)
        self.assertEqual(grant.user_id, legacy_id)

    def test_dual_open_subscription_rejects_before_owner_changes(self) -> None:
        canonical_id, legacy_id, subscription, invoice, grant = self._normalized_lineage()
        canonical = UserSubscription(
            id=uuid.uuid4(),
            user_id=canonical_id,
            plan_id=uuid.uuid4(),
            provider="creem",
            provider_subscription_id="sub_merge_2",
            normalized_status=NormalizedSubscriptionStatus.ACTIVE,
            product_code="creator_monthly",
            catalog_version_id=uuid.uuid4(),
            catalog_snapshot={"credits": 300},
        )
        with self.assertRaises(AccountMergeCreditError) as raised:
            select_subscription_projection_for_merge(
                canonical_user_id=canonical_id,
                legacy_user_id=legacy_id,
                subscriptions=[canonical, subscription],
                invoices=[invoice],
                grants=[grant],
                cancel_intents=[],
            )
        self.assertEqual(raised.exception.code, "dual_open_subscription")
        self.assertEqual(subscription.user_id, legacy_id)

    def test_partial_refund_pending_cancel_and_legacy_grant_each_block(self) -> None:
        canonical_id, legacy_id, subscription, invoice, grant = self._normalized_lineage()
        invoice.refunded_minor_units = 500
        with self.assertRaises(AccountMergeCreditError) as raised:
            select_subscription_projection_for_merge(
                canonical_user_id=canonical_id,
                legacy_user_id=legacy_id,
                subscriptions=[subscription],
                invoices=[invoice],
                grants=[grant],
                cancel_intents=[],
            )
        self.assertEqual(raised.exception.code, "subscription_refund_or_dispute_pending")

        invoice.refunded_minor_units = 0
        intent = SubscriptionCancelIntent(
            id=uuid.uuid4(),
            subscription_id=subscription.id,
            user_id=legacy_id,
            idempotency_key="merge-cancel",
            request_hash="b" * 64,
            provider_request_id="sc_merge",
            state=CancelIntentState.UNKNOWN,
        )
        with self.assertRaises(AccountMergeCreditError) as raised:
            select_subscription_projection_for_merge(
                canonical_user_id=canonical_id,
                legacy_user_id=legacy_id,
                subscriptions=[subscription],
                invoices=[invoice],
                grants=[grant],
                cancel_intents=[intent],
            )
        self.assertEqual(raised.exception.code, "subscription_cancellation_pending")

        grant.invoice_id = None
        with self.assertRaises(AccountMergeCreditError) as raised:
            select_subscription_projection_for_merge(
                canonical_user_id=canonical_id,
                legacy_user_id=legacy_id,
                subscriptions=[subscription],
                invoices=[invoice],
                grants=[grant],
                cancel_intents=[],
            )
        self.assertEqual(raised.exception.code, "subscription_lineage_not_normalized")


if __name__ == "__main__":
    unittest.main()
