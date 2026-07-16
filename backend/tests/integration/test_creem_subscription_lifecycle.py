"""Opt-in real Creem test-mode lifecycle proof.

The test deliberately fails closed unless a recorded fixture/export produced by
the real Provider test environment is supplied. Unit mocks cannot satisfy this
release gate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest


@unittest.skipUnless(
    os.environ.get("RUN_CREEM_TEST_MODE") == "1",
    "set RUN_CREEM_TEST_MODE=1 with CREEM_SUBSCRIPTION_EVIDENCE_PATH",
)
class CreemSubscriptionLifecycleIntegrationTest(unittest.TestCase):
    def test_real_test_mode_evidence_has_every_required_fact(self) -> None:
        path = Path(os.environ.get("CREEM_SUBSCRIPTION_EVIDENCE_PATH", ""))
        if not path.is_file():
            self.fail("CREEM_SUBSCRIPTION_EVIDENCE_PATH must be a real test-mode evidence file")
        evidence = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(evidence.get("provider"), "creem")
        self.assertEqual(evidence.get("mode"), "test")
        required = {
            "first_paid_transaction",
            "renewal_paid_transaction",
            "duplicate_replay",
            "out_of_order_replay",
            "past_due_no_grant",
            "recovery_new_transaction",
            "period_end_cancel",
            "full_invoice_refund",
            "partial_refund_reconciliation",
            "dispute_open_win_loss",
        }
        self.assertEqual(set(evidence.get("proofs") or {}), required)
        self.assertTrue(all((evidence["proofs"][name] or {}).get("passed") is True for name in required))


if __name__ == "__main__":
    unittest.main()
