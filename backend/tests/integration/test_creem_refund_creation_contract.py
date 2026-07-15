"""Refund initiation stays closed until an official Provider contract is proven."""

from __future__ import annotations

import unittest

from app.core.provider_contracts import CREEM_REFUND_CREATION, ProviderContractState


class CreemRefundCreationContractTest(unittest.TestCase):
    def test_refund_creation_is_code_versioned_unverified(self) -> None:
        self.assertEqual(CREEM_REFUND_CREATION.state, ProviderContractState.UNVERIFIED)
        self.assertIsNone(CREEM_REFUND_CREATION.endpoint_schema_sha256)
        self.assertIsNone(CREEM_REFUND_CREATION.test_evidence_sha256)


if __name__ == "__main__":
    unittest.main()
