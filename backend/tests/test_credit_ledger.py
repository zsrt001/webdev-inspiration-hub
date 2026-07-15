"""Commercial credit authority and legacy-mutation retirement tests."""

from __future__ import annotations

import unittest
import uuid

from app.models.credit_transaction import CreditTransactionType
from app.models.user_credit import UserCredit
from app.services.credit_service import (
    CreditAuthorityRequired,
    add_credits_async,
    add_credits_with_transaction_async,
    deduct_credits_async,
    grant_welcome_bonus,
    refund_generation_credits_once_async,
    reset_balance_async,
)


class _NoTouchDb:
    def __getattr__(self, name):
        raise AssertionError(f"retired mutation touched database: {name}")


class CreditLedgerAuthorityTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_balance_mutations_fail_before_database_access(self) -> None:
        db = _NoTouchDb()
        user_id = uuid.uuid4()
        calls = (
            grant_welcome_bonus(db, user_id),
            deduct_credits_async(db, user_id, 2),
            add_credits_async(
                db,
                user_id,
                50,
                transaction_type=CreditTransactionType.PURCHASE,
            ),
            add_credits_with_transaction_async(
                db,
                user_id,
                50,
                transaction_type=CreditTransactionType.ADMIN_GRANT,
            ),
            refund_generation_credits_once_async(
                db,
                user_id,
                2,
                order_id=uuid.uuid4(),
            ),
            reset_balance_async(db, user_id, 10),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaises(CreditAuthorityRequired):
                await call

    def test_accounting_balance_can_be_debt_but_spendable_never_negative(self) -> None:
        credit = UserCredit(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            balance=-7,
            reserved_balance=0,
        )
        self.assertEqual(credit.accounting_balance, -7)
        self.assertEqual(credit.debt, 7)
        self.assertEqual(credit.spendable_balance, 0)

        credit.balance = 10
        credit.reserved_balance = 4
        self.assertEqual(credit.accounting_balance, 10)
        self.assertEqual(credit.spendable_balance, 6)

    def test_generation_debit_and_positive_grants_have_distinct_authorities(self) -> None:
        self.assertEqual(CreditTransactionType.GENERATION_DEBIT.value, "GENERATION_DEBIT")
        self.assertEqual(CreditTransactionType.WELCOME_BONUS.value, "WELCOME_BONUS")
        self.assertEqual(CreditTransactionType.PURCHASE.value, "PURCHASE")
        self.assertEqual(CreditTransactionType.ADMIN_GRANT.value, "ADMIN_GRANT")


if __name__ == "__main__":
    unittest.main()
