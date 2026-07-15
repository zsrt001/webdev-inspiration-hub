"""Controlled legacy-account claim and empty-account merge contracts."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.account_claim_proof import AccountClaimProof, AccountClaimProofType  # noqa: E402
from app.models.auth_refresh_token import AuthRefreshToken  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus  # noqa: E402
from app.models.credit_transaction import CreditTransaction  # noqa: E402
from app.models.live_portrait_job import LivePortraitJob  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.payment_event import PaymentEvent  # noqa: E402
from app.models.subscription_credit_grant import SubscriptionCreditGrant  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_account_merge import UserAccountMerge  # noqa: E402
from app.models.user_credit import UserCredit  # noqa: E402
from app.models.user_identity import UserIdentity  # noqa: E402
from app.models.user_subscription import UserSubscription  # noqa: E402
from app.services.account_claim_proof_service import (  # noqa: E402
    record_support_claim_proof,
    verify_payment_claim_reference,
)
from app.services.account_merge_service import AccountClaimError, claim_legacy_account  # noqa: E402


def _statement_entity(statement):
    for description in getattr(statement, "column_descriptions", ()):  # SQLAlchemy Select
        entity = description.get("entity")
        if entity is not None:
            return entity
    return None


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _FakeDb:
    """Small query-result adapter; assertions target real service state changes."""

    def __init__(self, *, scalars=None, rows=None):
        self._scalar_results = {
            model: deque(values) for model, values in (scalars or {}).items()
        }
        self._row_results = {
            model: deque([list(value) for value in values])
            for model, values in (rows or {}).items()
        }
        self.added = []
        self.deleted = []

    async def scalar(self, statement):
        queue = self._scalar_results.get(_statement_entity(statement), deque())
        return queue.popleft() if queue else None

    async def scalars(self, statement):
        queue = self._row_results.get(_statement_entity(statement), deque())
        return _Rows(queue.popleft() if queue else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def refresh(self, _value):
        return None

    async def delete(self, value):
        self.deleted.append(value)


NOW = datetime(2026, 7, 13, 17, 0, tzinfo=timezone.utc)


def _account_fixture():
    canonical = User(
        id=uuid4(), status="active", role="user", email="canonical@example.com"
    )
    legacy = User(
        id=uuid4(), status="active", role="user", email="legacy@example.com",
        username="legacy", password="legacy-hash", openid="legacy-openid",
        auth_provider="legacy", auth_subject="legacy-subject", nickname="Legacy",
    )
    identity = UserIdentity(
        id=uuid4(), user_id=canonical.id, provider="supabase", subject="google-subject",
        verified_email_snapshot=canonical.email,
    )
    proof = AccountClaimProof(
        id=uuid4(), canonical_user_id=canonical.id, legacy_user_id=legacy.id,
        proof_type=AccountClaimProofType.VERIFIED_PAYMENT,
        external_reference_hash="a" * 64, verifier_actor="payment-verifier:creem",
        verified_at=NOW, expires_at=NOW + timedelta(minutes=15),
        audit_request_id="req-proof",
    )
    return canonical, legacy, identity, proof


def _merge_db(*, footprint_model=None, proof_override=True, graph=None, sessions=None, tokens=None):
    canonical, legacy, identity, proof = _account_fixture()
    rows = {
        User: [[canonical, legacy]],
        UserIdentity: [[identity]],
        UserAccountMerge: [list(graph or [])],
        UserCredit: [[]],
        CreditTransaction: [[]],
        CreditPurchase: [[]],
        UserSubscription: [[]],
        SubscriptionCreditGrant: [[]],
        Order: [[]],
        LivePortraitJob: [[]],
        AuthSession: [list(sessions or [])],
        AuthRefreshToken: [list(tokens or [])],
    }
    if footprint_model is not None:
        rows[footprint_model] = [[object()]]
    selected_proof = proof if proof_override is True else proof_override
    db = _FakeDb(scalars={AccountClaimProof: [selected_proof]}, rows=rows)
    return db, canonical, legacy, identity, proof


class AccountMergeInterfaceTest(unittest.TestCase):
    def test_task8_account_claim_interfaces_exist(self) -> None:
        expected = {
            "app.services.account_claim_proof_service": (
                "verify_payment_claim_reference",
                "record_support_claim_proof",
            ),
            "app.services.account_merge_service": (
                "AccountClaimError",
                "claim_legacy_account",
            ),
        }
        for module_name, names in expected.items():
            with self.subTest(module=module_name):
                self.assertIsNotNone(find_spec(module_name), f"missing {module_name}")
                module = import_module(module_name)
                for name in names:
                    self.assertTrue(hasattr(module, name), f"missing {module_name}.{name}")

    def test_public_account_claim_routes_are_post_only_and_support_proof_stays_internal(self) -> None:
        from app.routers import api_router
        from tests.route_contract import effective_routes

        routes = {
            (route.path, frozenset(route.methods or set()))
            for route in effective_routes(api_router)
        }
        self.assertIn(
            ("/auth/account-claims/payment-proof", frozenset({"POST"})),
            routes,
        )
        self.assertIn(
            ("/auth/account-claims/merge", frozenset({"POST"})),
            routes,
        )
        router_path = BACKEND_DIR / "app/routers/auth/account_claim.py"
        self.assertTrue(router_path.is_file())
        source = router_path.read_text(encoding="utf-8")
        self.assertIn("get_session_user", source)
        self.assertNotIn("record_support_claim_proof", source)

    def test_account_page_exposes_controlled_payment_claim_flow(self) -> None:
        source = (BACKEND_DIR.parent / "frontend/src/pages/account/index.vue").read_text(
            encoding="utf-8"
        )
        self.assertIn("/auth/account-claims/payment-proof", source)
        self.assertIn("/auth/account-claims/merge", source)
        self.assertIn("legacyAccountId", source)
        self.assertIn("paymentReference", source)


class AccountClaimProofTest(unittest.IsolatedAsyncioTestCase):
    async def test_arbitrary_or_unsigned_payment_reference_creates_no_proof(self) -> None:
        canonical, legacy, _identity, _proof = _account_fixture()
        purchase = CreditPurchase(
            id=uuid4(), user_id=legacy.id, provider="creem", package_id="pack",
            credits=50, price_cents=900, currency="USD",
            status=CreditPurchaseStatus.PAID, provider_request_id="request-1",
            provider_payment_id="payment-1", webhook_event_id="event-1",
        )
        unsigned = PaymentEvent(
            id=uuid4(), provider="creem", event_id="event-1", event_type="checkout.completed",
            processed_at=None, error=None,
        )
        for reference, event in (("unknown", None), ("payment-1", unsigned)):
            db = _FakeDb(
                scalars={PaymentEvent: [event]}, rows={CreditPurchase: [[purchase]]}
            )
            with self.subTest(reference=reference):
                with self.assertRaises(AccountClaimError) as raised:
                    await verify_payment_claim_reference(
                        db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                        payment_reference=reference, audit_request_id="req-payment", now=NOW,
                    )
                self.assertEqual(raised.exception.code, "ownership_proof_not_verified")
                self.assertFalse(any(isinstance(item, AccountClaimProof) for item in db.added))

    async def test_unpaid_or_wrong_owner_payment_reference_creates_no_proof(self) -> None:
        canonical, legacy, _identity, _proof = _account_fixture()
        for status, owner in (
            (CreditPurchaseStatus.PENDING, legacy.id),
            (CreditPurchaseStatus.PAID, uuid4()),
        ):
            purchase = CreditPurchase(
                id=uuid4(), user_id=owner, provider="creem", package_id="pack",
                credits=50, price_cents=900, currency="USD", status=status,
                provider_request_id="request-1", provider_payment_id="payment-1",
                webhook_event_id="event-1",
            )
            event = PaymentEvent(
                id=uuid4(), provider="creem", event_id="event-1",
                event_type="checkout.completed", processed_at=NOW, error=None,
            )
            db = _FakeDb(scalars={PaymentEvent: [event]}, rows={CreditPurchase: [[purchase]]})
            with self.subTest(status=status, owner=owner), self.assertRaises(AccountClaimError):
                await verify_payment_claim_reference(
                    db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                    payment_reference="payment-1", audit_request_id="req-payment", now=NOW,
                )
            self.assertFalse(db.added)

    async def test_signed_paid_payment_fact_creates_hash_only_bound_proof(self) -> None:
        canonical, legacy, _identity, _proof = _account_fixture()
        purchase = CreditPurchase(
            id=uuid4(), user_id=legacy.id, provider="creem", package_id="pack",
            credits=50, price_cents=900, currency="USD", status=CreditPurchaseStatus.PAID,
            provider_request_id="request-1", provider_payment_id="payment-1",
            webhook_event_id="event-1",
        )
        event = PaymentEvent(
            id=uuid4(), provider="creem", event_id="event-1", event_type="checkout.completed",
            processed_at=NOW, error=None,
        )
        db = _FakeDb(scalars={PaymentEvent: [event]}, rows={CreditPurchase: [[purchase]]})

        proof = await verify_payment_claim_reference(
            db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
            payment_reference="  payment-1  ", audit_request_id="req-payment", now=NOW,
        )

        self.assertIsInstance(proof, AccountClaimProof)
        self.assertEqual(proof.canonical_user_id, canonical.id)
        self.assertEqual(proof.legacy_user_id, legacy.id)
        self.assertEqual(proof.proof_type, AccountClaimProofType.VERIFIED_PAYMENT)
        self.assertEqual(len(proof.external_reference_hash), 64)
        self.assertNotIn("payment-1", proof.external_reference_hash)
        self.assertEqual(proof.expires_at, NOW + timedelta(minutes=15))

    async def test_support_proof_requires_database_admin_monitored_channel_and_audit_hash(self) -> None:
        canonical, legacy, _identity, _proof = _account_fixture()
        ordinary = User(id=uuid4(), status="active", role="user")
        admin = User(id=uuid4(), status="active", role="admin")
        invalid_inputs = (
            {"admin_user": ordinary, "monitored_support_channel": "support@example.com", "audit_evidence_hash": "b" * 64},
            {"admin_user": admin, "monitored_support_channel": "", "audit_evidence_hash": "b" * 64},
            {"admin_user": admin, "monitored_support_channel": "support@example.com", "audit_evidence_hash": "not-a-hash"},
        )
        for changed in invalid_inputs:
            db = _FakeDb()
            with self.subTest(changed=changed), self.assertRaises(AccountClaimError):
                await record_support_claim_proof(
                    db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                    support_case_reference="CASE-123", audit_request_id="req-support",
                    now=NOW, **changed,
                )
            self.assertFalse(db.added)

        db = _FakeDb()
        proof = await record_support_claim_proof(
            db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
            support_case_reference="CASE-123", audit_evidence_hash="b" * 64,
            admin_user=admin, monitored_support_channel="support@example.com",
            audit_request_id="req-support", now=NOW,
        )
        self.assertIsInstance(proof, AccountClaimProof)
        self.assertEqual(proof.proof_type, AccountClaimProofType.VERIFIED_SUPPORT_CASE)
        self.assertEqual(proof.verifier_actor, f"database-admin:{admin.id}")
        self.assertNotIn("CASE-123", proof.external_reference_hash)


class AccountMergePolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_service_does_not_overwrite_database_trigger_consumption(self) -> None:
        source = (BACKEND_DIR / "app/services/account_merge_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("await db.refresh(proof)", source)
        self.assertNotIn("proof.consumed_at =", source)
        self.assertNotIn("proof.consumed_by_merge_id =", source)

    async def test_legacy_jwt_or_email_alone_cannot_claim_account(self) -> None:
        db, canonical, legacy, _identity, _proof = _merge_db(proof_override=None)
        with self.assertRaises(AccountClaimError) as raised:
            await claim_legacy_account(
                db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                verified_proof_id=None, audit_request_id="req-merge", now=NOW,
            )
        self.assertEqual(raised.exception.code, "ownership_proof_required")

    async def test_mismatched_expired_or_consumed_proof_fails_closed(self) -> None:
        for mutation, expected in (
            ("mismatch", "ownership_proof_mismatch"),
            ("expired", "ownership_proof_expired"),
            ("consumed", "ownership_proof_consumed"),
        ):
            db, canonical, legacy, _identity, proof = _merge_db()
            if mutation == "mismatch":
                proof.legacy_user_id = uuid4()
            elif mutation == "expired":
                proof.expires_at = NOW
            else:
                proof.consumed_at = NOW
                proof.consumed_by_merge_id = uuid4()
            with self.subTest(mutation=mutation):
                with self.assertRaises(AccountClaimError) as raised:
                    await claim_legacy_account(
                        db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                        verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
                    )
                self.assertEqual(raised.exception.code, expected)

    async def test_self_merge_and_merge_graph_chain_are_forbidden(self) -> None:
        db, canonical, _legacy, _identity, proof = _merge_db()
        with self.assertRaises(AccountClaimError) as raised:
            await claim_legacy_account(
                db, canonical_user_id=canonical.id, legacy_user_id=canonical.id,
                verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
            )
        self.assertEqual(raised.exception.code, "self_merge_forbidden")

        db, canonical, legacy, _identity, proof = _merge_db()
        graph_edge = UserAccountMerge(
            id=uuid4(), canonical_user_id=legacy.id, legacy_user_id=uuid4(),
            claim_proof_id=uuid4(), audit_request_id="prior",
        )
        db._row_results[UserAccountMerge] = deque([[graph_edge]])
        with self.assertRaises(AccountClaimError) as raised:
            await claim_legacy_account(
                db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
            )
        self.assertEqual(raised.exception.code, "merge_graph_conflict")

    async def test_normalized_commercial_footprint_uses_compensation_merge(self) -> None:
        for model in (
            UserCredit,
            CreditTransaction,
            CreditPurchase,
            Order,
            UserSubscription,
            SubscriptionCreditGrant,
        ):
            db, canonical, legacy, _identity, proof = _merge_db(footprint_model=model)
            with self.subTest(model=model.__name__), patch(
                "app.services.account_merge_service.merge_credit_accounts",
                new=AsyncMock(),
            ) as merge_credits:
                merge = await claim_legacy_account(
                        db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                        verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
                )
                self.assertIsInstance(merge, UserAccountMerge)
                merge_credits.assert_awaited_once_with(
                    db,
                    canonical_user_id=canonical.id,
                    legacy_user_id=legacy.id,
                    request_id="req-merge",
                )
                self.assertEqual(legacy.status, "merged")

    async def test_retired_feature_blocks_without_mutation(self) -> None:
        expected_by_model = {
            LivePortraitJob: "legacy_feature_reconciliation_required",
        }
        for model, expected in expected_by_model.items():
            db, canonical, legacy, _identity, proof = _merge_db(footprint_model=model)
            original_email = legacy.email
            with self.subTest(model=model.__name__), self.assertRaises(AccountClaimError) as raised:
                await claim_legacy_account(
                    db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                    verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
                )
            self.assertEqual(raised.exception.code, expected)
            self.assertEqual(legacy.email, original_email)
            self.assertFalse(any(isinstance(item, UserAccountMerge) for item in db.added))

    async def test_empty_legacy_profile_merges_once_and_consumes_proof(self) -> None:
        db, canonical, legacy, _identity, proof = _merge_db()
        merge = await claim_legacy_account(
            db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
            verified_proof_id=proof.id, audit_request_id="req-merge", now=NOW,
        )
        self.assertIsInstance(merge, UserAccountMerge)
        self.assertEqual(merge.canonical_user_id, canonical.id)
        self.assertEqual(merge.legacy_user_id, legacy.id)
        self.assertEqual(legacy.status, "merged")
        self.assertIsNone(legacy.email)
        self.assertIsNone(legacy.password)
        self.assertFalse(db.deleted)

        proof.consumed_at = NOW
        proof.consumed_by_merge_id = merge.id
        replay_db = _FakeDb(
            scalars={AccountClaimProof: [proof]},
            rows={User: [[canonical, legacy]]},
        )
        with self.assertRaises(AccountClaimError) as raised:
            await claim_legacy_account(
                replay_db, canonical_user_id=canonical.id, legacy_user_id=legacy.id,
                verified_proof_id=proof.id, audit_request_id="req-merge-2", now=NOW,
            )
        self.assertEqual(raised.exception.code, "ownership_proof_consumed")


if __name__ == "__main__":
    unittest.main()
