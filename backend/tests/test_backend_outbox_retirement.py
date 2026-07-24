"""Generation execution no longer persists or consumes queue wake-up rows."""

from __future__ import annotations

import unittest
from pathlib import Path


class BackendOutboxRetirementTest(unittest.TestCase):
    def test_generation_path_has_no_queue_outbox_dependency(self) -> None:
        backend = Path(__file__).resolve().parents[1]
        for relative in (
            "app/services/order_transaction_service.py",
            "app/services/generation_repair_service.py",
            "app/services/generation_executor_service.py",
        ):
            source = (backend / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("OutboxEvent", source)
                self.assertNotIn("_mark_outbox_dispatched", source)


if __name__ == "__main__":
    unittest.main()
