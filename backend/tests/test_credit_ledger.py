"""Credit ledger service tests."""

from pathlib import Path
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.credit_transaction import CreditTransactionType  # noqa: E402
from app.models.user_credit import UserCredit  # noqa: E402
from app.services.credit_service import (  # noqa: E402
    DEFAULT_CREDITS,
    COST_COUPLE_REMOTE_GENERATION,
    COST_SINGLE_GENERATION,
    add_credits_async,
    deduct_credits_async,
    grant_welcome_bonus,
    get_balance_async,
)
from app.services.trial_access_service import trial_generation_allowed  # noqa: E402


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return []


class _FakeDb:
    def __init__(self):
        self.credit_row = None
        self.transactions = []

    async def execute(self, _statement):
        statement_text = str(_statement)
        if "credit_transactions" in statement_text:
            for transaction in self.transactions:
                if transaction.transaction_type == CreditTransactionType.WELCOME_BONUS:
                    return _ScalarResult(transaction)
            return _ScalarResult(None)
        return _ScalarResult(self.credit_row)

    def add(self, value):
        if isinstance(value, UserCredit):
            self.credit_row = value
        else:
            self.transactions.append(value)

    async def flush(self):
        return None


class CreditLedgerTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_credit_account_records_welcome_bonus(self) -> None:
        db = _FakeDb()
        user_id = uuid.uuid4()

        granted = await grant_welcome_bonus(db, user_id)
        balance = await get_balance_async(db, user_id)

        self.assertTrue(granted)
        self.assertEqual(balance, DEFAULT_CREDITS)
        self.assertEqual(len(db.transactions), 1)
        self.assertEqual(db.transactions[0].transaction_type, CreditTransactionType.WELCOME_BONUS)
        self.assertEqual(db.transactions[0].amount, DEFAULT_CREDITS)
        self.assertEqual(db.transactions[0].balance_after, DEFAULT_CREDITS)

    async def test_deduct_records_negative_generation_debit(self) -> None:
        db = _FakeDb()
        user_id = uuid.uuid4()
        await grant_welcome_bonus(db, user_id)

        success = await deduct_credits_async(db, user_id, 2)

        self.assertTrue(success)
        self.assertEqual(db.credit_row.balance, DEFAULT_CREDITS - 2)
        self.assertEqual(db.transactions[-1].transaction_type, CreditTransactionType.GENERATION_DEBIT)
        self.assertEqual(db.transactions[-1].amount, -2)
        self.assertEqual(db.transactions[-1].balance_after, DEFAULT_CREDITS - 2)

    async def test_failed_deduct_does_not_write_transaction(self) -> None:
        db = _FakeDb()
        user_id = uuid.uuid4()
        await grant_welcome_bonus(db, user_id)

        success = await deduct_credits_async(db, user_id, DEFAULT_CREDITS + 1)

        self.assertFalse(success)
        self.assertEqual(len(db.transactions), 1)
        self.assertEqual(db.credit_row.balance, DEFAULT_CREDITS)

    async def test_add_records_purchase_credit(self) -> None:
        db = _FakeDb()
        user_id = uuid.uuid4()
        await grant_welcome_bonus(db, user_id)

        balance = await add_credits_async(
            db,
            user_id,
            50,
            transaction_type=CreditTransactionType.PURCHASE,
            source="credit_purchase",
            source_id="purchase-1",
        )

        self.assertEqual(balance, DEFAULT_CREDITS + 50)
        self.assertEqual(db.transactions[-1].transaction_type, CreditTransactionType.PURCHASE)
        self.assertEqual(db.transactions[-1].amount, 50)
        self.assertEqual(db.transactions[-1].source, "credit_purchase")
        self.assertEqual(db.transactions[-1].source_id, "purchase-1")

    async def test_starter_credits_only_allow_base_single_generation(self) -> None:
        self.assertTrue(
            trial_generation_allowed(
                template_category=None,
                image_count=1,
                credits_cost=COST_SINGLE_GENERATION,
            )
        )
        self.assertFalse(
            trial_generation_allowed(
                template_category=None,
                image_count=2,
                is_remote_join=True,
                credits_cost=COST_COUPLE_REMOTE_GENERATION,
            )
        )


if __name__ == "__main__":
    unittest.main()
