"""Regression guard for parent-before-child credit grant persistence."""

from __future__ import annotations

from pathlib import Path
import unittest


class CreditGrantInsertOrderTest(unittest.TestCase):
    def test_every_root_grant_flushes_transaction_before_grant_lot(self) -> None:
        root = Path(__file__).resolve().parents[2]
        cases = (
            (
                "backend/app/services/welcome_grant_service.py",
                "async def ensure_welcome_grant_for_identity(",
                "db.add(transaction)\n    await db.flush()\n    db.add(lot)",
            ),
            (
                "backend/app/services/admin_service.py",
                "async def adjust_user_credits_by_admin(",
                "db.add(transaction)\n        await db.flush()\n        db.add(lot)",
            ),
            (
                "backend/app/services/credit_reservation_service.py",
                "async def refund_captured_reservation(",
                "db.add(transaction)\n    await db.flush()\n    db.add(lot)",
            ),
            (
                "backend/app/services/payment_service.py",
                "async def _apply_checkout_capture(",
                "db.add(root)\n        await db.flush()\n        db.add(lot)",
            ),
            (
                "backend/app/services/subscription_service.py",
                "async def apply_subscription_paid_transaction(",
                "db.add(transaction)\n        await db.flush()\n        db.add(lot)",
            ),
        )

        for relative_path, function_marker, required_sequence in cases:
            source = (root / relative_path).read_text(encoding="utf-8")
            function_source = source[source.index(function_marker) :]
            with self.subTest(relative_path=relative_path):
                self.assertIn(required_sequence, function_source)


if __name__ == "__main__":
    unittest.main()
