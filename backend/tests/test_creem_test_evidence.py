"""Signed Creem test evidence is validated directly, without activation contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import unittest

from scripts.release.verify_creem_test_evidence import (
    CREEM_CASES,
    canonical_json_bytes,
    verify_creem_test_evidence,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
SOURCE = "a" * 40
KEY = b"creem-test-evidence-signing-key-32-bytes"


def _bundle() -> dict:
    official_contracts = {}
    sandbox_reports = {}
    source_by_capability = {
        "dashboard_refund_confirmation": "https://docs.creem.io/features/subscriptions/refunds-and-cancellations",
        "subscription_paid_transaction": "https://docs.creem.io/code/webhooks",
        "subscription_period_end_cancellation": "https://docs.creem.io/api-reference/endpoint/cancel-subscription",
    }
    for index, (name, spec) in enumerate(CREEM_CASES.items(), start=1):
        capability = spec["capability"]
        official = {
            "schema": "vowpic.provider-official-contract.v1",
            "provider": "creem",
            "capability": capability,
            "official_source_url": source_by_capability[capability],
            "official_version": "2026-07-22",
            "endpoint_schema_sha256": f"{index}" * 64,
            "authentication_scheme": "signed webhook or x-api-key authentication",
            "correlation_semantics": "provider identifiers bind every local intent and event",
            "idempotency_semantics": "local idempotency keys and unique provider event identifiers",
            "retry_taxonomy": "ambiguous writes enter reconciliation and are not replayed",
            "retrieved_at": NOW.isoformat(),
        }
        official_hash = hashlib.sha256(canonical_json_bytes(official)).hexdigest()
        unsigned = {
            "schema": "vowpic.creem-sandbox-report.v1",
            "provider": "creem",
            "capability": capability,
            "tested_source_sha": SOURCE,
            "official_contract_sha256": official_hash,
            "passed": True,
            "proof_id_hash": f"{index + 3}" * 64,
            "request_count": 1,
            "replay_count": 1,
            "duplicate_effect_count": 0,
            "verified_facts": {fact: True for fact in spec["required_facts"]},
            "produced_at": NOW.isoformat(),
            "approval_ref": "creem-test-run-1",
        }
        signature = hmac.new(KEY, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
        official_contracts[name] = official
        sandbox_reports[name] = {**unsigned, "signature": f"hmac-sha256:{signature}"}
    return {
        "schema": "vowpic.creem-test-evidence.v1",
        "official_contracts": official_contracts,
        "sandbox_reports": sandbox_reports,
    }


class CreemTestEvidenceTest(unittest.TestCase):
    def test_complete_signed_test_bundle_passes(self) -> None:
        hashes = verify_creem_test_evidence(
            _bundle(), expected_source_sha=SOURCE, signing_key=KEY, now=NOW
        )
        self.assertEqual(set(hashes), set(CREEM_CASES))
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))

    def test_wrong_source_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "required behavior"):
            verify_creem_test_evidence(
                _bundle(), expected_source_sha="b" * 40, signing_key=KEY, now=NOW
            )


if __name__ == "__main__":
    unittest.main()
