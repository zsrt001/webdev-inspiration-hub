"""Sandbox proof gate for lost-response correlation or provider idempotency."""

from __future__ import annotations

import os
import unittest

from app.core.provider_contracts import (
    EVOLINK_SUBMISSION_RECONCILIATION,
    require_verified_provider_contract,
)


@unittest.skipUnless(os.getenv("RUN_EVOLINK_SANDBOX") == "1", "Evolink sandbox proof NOT_RUN")
class EvolinkSubmissionReconciliationIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_lost_post_response_resolves_one_task_without_repost(self) -> None:
        require_verified_provider_contract(EVOLINK_SUBMISSION_RECONCILIATION)
        self.fail("sandbox evidence harness requires approved credentials and isolated callback origin")


if __name__ == "__main__":
    unittest.main()
