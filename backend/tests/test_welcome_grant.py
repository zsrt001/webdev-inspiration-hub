"""Identity-bound welcome grant tests."""

from __future__ import annotations

import unittest
import uuid

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.user_credit import UserCredit
from app.models.user_identity import UserIdentity
from app.models.welcome_grant_claim import WelcomeGrantClaim
from app.services.welcome_grant_service import (
    WELCOME_GRANT_AMOUNT,
    WelcomeGrantError,
    ensure_welcome_grant_for_identity,
)
from pathlib import Path


class _Db:
    def __init__(self, identity: UserIdentity, credit: UserCredit | None = None) -> None:
        self.identity = identity
        self.credit = credit
        self.claim: WelcomeGrantClaim | None = None
        self.transactions: list[CreditTransaction] = []
        self.lots: list[CreditGrantLot] = []
        self.persisted_transaction_ids: set[uuid.UUID] = set()
        self.persisted_lot_ids: set[uuid.UUID] = set()

    async def scalar(self, statement):
        source = str(statement)
        if "user_identities" in source:
            return self.identity
        if "welcome_grant_claims" in source:
            return self.claim
        if "user_credits" in source:
            return self.credit
        raise AssertionError(source)

    def add(self, value):
        if isinstance(value, UserCredit):
            self.credit = value
        elif isinstance(value, CreditTransaction):
            self.transactions.append(value)
        elif isinstance(value, CreditGrantLot):
            self.lots.append(value)
        elif isinstance(value, WelcomeGrantClaim):
            self.claim = value
        else:
            raise AssertionError(type(value))

    async def flush(self):
        for lot in self.lots:
            if (
                lot.id not in self.persisted_lot_ids
                and lot.root_transaction_id not in self.persisted_transaction_ids
            ):
                raise AssertionError("grant_lot_flushed_before_root_transaction")
        if self.claim is not None:
            if self.claim.credit_transaction_id not in self.persisted_transaction_ids:
                raise AssertionError("welcome_claim_flushed_before_root_transaction")
            if self.claim.grant_lot_id not in self.persisted_lot_ids:
                raise AssertionError("welcome_claim_flushed_before_grant_lot")
        self.persisted_transaction_ids.update(item.id for item in self.transactions)
        self.persisted_lot_ids.update(item.id for item in self.lots)
        return None


class WelcomeGrantTest(unittest.IsolatedAsyncioTestCase):
    def test_session_issuance_creates_welcome_lineage_before_session_row(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (root / "backend/app/services/auth_session_service.py").read_text(
            encoding="utf-8"
        )
        grant_call = source.index("await ensure_welcome_grant_for_identity(")
        session_create = source.index("session = AuthSession(", grant_call)
        self.assertLess(grant_call, session_create)

    async def test_verified_identity_receives_exactly_one_root_lot_and_claim(self) -> None:
        user_id = uuid.uuid4()
        identity = UserIdentity(
            id=uuid.uuid4(),
            user_id=user_id,
            provider="supabase",
            subject="subject-1",
            verified_email_snapshot="person@example.com",
        )
        db = _Db(identity)

        first = await ensure_welcome_grant_for_identity(db, identity_id=identity.id)
        second = await ensure_welcome_grant_for_identity(db, identity_id=identity.id)

        self.assertIs(first, second)
        self.assertEqual(db.credit.balance, WELCOME_GRANT_AMOUNT)
        self.assertEqual(len(db.transactions), 1)
        self.assertEqual(len(db.lots), 1)
        self.assertEqual(db.transactions[0].transaction_type, CreditTransactionType.WELCOME_BONUS)
        self.assertEqual(db.transactions[0].amount, WELCOME_GRANT_AMOUNT)
        self.assertEqual(db.transactions[0].root_transaction_id, db.transactions[0].id)
        self.assertEqual(db.lots[0].source_type, GrantLotSourceType.WELCOME)
        self.assertEqual(db.lots[0].root_transaction_id, db.transactions[0].id)
        self.assertEqual(db.claim.credit_transaction_id, db.transactions[0].id)
        self.assertEqual(db.claim.grant_lot_id, db.lots[0].id)

    async def test_unverified_or_revoked_identity_changes_nothing(self) -> None:
        for email, revoked in ((None, None), ("person@example.com", object())):
            identity = UserIdentity(
                id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="supabase",
                subject="subject-1",
                verified_email_snapshot=email,
                revoked_at=revoked,
            )
            db = _Db(identity)
            with self.subTest(email=email, revoked=revoked), self.assertRaises(WelcomeGrantError):
                await ensure_welcome_grant_for_identity(db, identity_id=identity.id)
            self.assertEqual(db.transactions, [])
            self.assertEqual(db.lots, [])
            self.assertIsNone(db.claim)


if __name__ == "__main__":
    unittest.main()
