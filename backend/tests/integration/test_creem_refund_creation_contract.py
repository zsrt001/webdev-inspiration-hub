"""Refund initiation stays closed until an official Provider contract is proven."""

from __future__ import annotations

import unittest

from app.core.provider_contracts import CREEM_REFUND_CREATION, ProviderContractState
from app.services.payment_service import PaymentError, payment_service


class CreemRefundCreationContractTest(unittest.IsolatedAsyncioTestCase):
    def test_refund_creation_is_code_versioned_unverified(self) -> None:
        self.assertEqual(CREEM_REFUND_CREATION.state, ProviderContractState.UNVERIFIED)
        self.assertIsNone(CREEM_REFUND_CREATION.endpoint_schema_sha256)
        self.assertIsNone(CREEM_REFUND_CREATION.test_evidence_sha256)

    async def test_refund_service_fails_closed_without_a_documented_api(self) -> None:
        with self.assertRaises(PaymentError) as raised:
            await payment_service.initiate_refund()
        self.assertEqual(raised.exception.code, "provider_refund_creation_unverified")
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
