"""Code-versioned Provider contract authority and evidence activation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SCRIPT = ROOT / "scripts" / "release" / "activate_provider_contracts.py"
READINESS = ROOT / "scripts" / "release" / "verify_commercial_provider_readiness.py"
CONTRACT = ROOT / "release" / "provider-contracts.json"


def _module():
    if not SCRIPT.exists():
        raise AssertionError("Provider contract activator is missing")
    spec = importlib.util.spec_from_file_location("activate_provider_contracts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readiness_module():
    spec = importlib.util.spec_from_file_location(
        "verify_commercial_provider_readiness",
        READINESS,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _official_contract(now: datetime) -> dict[str, object]:
    return {
        "schema": "vowpic.provider-official-contract.v1",
        "provider": "evolink",
        "capability": "submission_reconciliation",
        "official_source_url": (
            "https://docs.evolink.ai/en/api-manual/image-series/"
            "nanobanana/nanobanana-2-image-generate"
        ),
        "official_version": "retrieved-2026-07-19",
        "endpoint_schema_sha256": "a" * 64,
        "correlation_semantics": (
            "an attempt-bound HTTPS callback receives the terminal task-query payload "
            "and its stable task ID after billing confirmation"
        ),
        "idempotency_semantics": (
            "EvoLink retries a failed callback up to three times while the attempt-bound "
            "receiver accepts one matching task ID and rejects conflicting IDs"
        ),
        "retrieved_at": now.isoformat(),
    }


def _signed_report(
    module,
    *,
    now: datetime,
    tested_source_sha: str,
    official_sha: str,
    signing_key: bytes,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "vowpic.evolink-sandbox-report.v1",
        "provider": "evolink",
        "capability": "submission_reconciliation",
        "tested_source_sha": tested_source_sha,
        "official_contract_sha256": official_sha,
        "passed": True,
        "lost_response_simulated": True,
        "recovered_task_id_hash": "b" * 64,
        "submit_count": 1,
        "duplicate_generation_count": 0,
        "produced_at": now.isoformat(),
        "approval_ref": "provider-approval-123",
    }
    signature = hmac.new(signing_key, module.canonical_json_bytes(report), hashlib.sha256).hexdigest()
    report["signature"] = f"hmac-sha256:{signature}"
    return report


def _creem_bundle(
    module,
    *,
    document: dict,
    now: datetime,
    source_sha: str,
    signing_key: bytes,
) -> dict:
    official_contracts = {}
    sandbox_reports = {}
    for index, (name, spec) in enumerate(module.CREEM_CONTRACTS.items(), start=1):
        official = {
            "schema": "vowpic.provider-official-contract.v1",
            "provider": "creem",
            "capability": spec["capability"],
            "official_source_url": f"https://docs.creem.io/api-reference/{spec['capability']}",
            "official_version": "2026-07-19",
            "endpoint_schema_sha256": f"{index:x}" * 64,
            "authentication_scheme": "x-api-key header with test-mode API credentials",
            "correlation_semantics": "stable local request reference binds the Provider object",
            "idempotency_semantics": "replay returns the same effect without a duplicate mutation",
            "retry_taxonomy": "timeouts remain unknown until an authoritative Provider query resolves them",
            "retrieved_at": now.isoformat(),
        }
        official_hash = hashlib.sha256(
            module.canonical_json_bytes(official)
        ).hexdigest()
        report = {
            "schema": "vowpic.creem-sandbox-report.v1",
            "provider": "creem",
            "capability": spec["capability"],
            "tested_source_sha": source_sha,
            "official_contract_sha256": official_hash,
            "passed": True,
            "proof_id_hash": f"{index + 4:x}" * 64,
            "request_count": 1,
            "replay_count": 1,
            "duplicate_effect_count": 0,
            "verified_facts": {
                fact: True for fact in sorted(spec["required_facts"])
            },
            "produced_at": now.isoformat(),
            "approval_ref": "provider-approval-123",
        }
        signature = hmac.new(
            signing_key,
            module.canonical_json_bytes(report),
            hashlib.sha256,
        ).hexdigest()
        report["signature"] = f"hmac-sha256:{signature}"
        official_contracts[name] = official
        sandbox_reports[name] = report
    return {
        "schema": "vowpic.creem-activation-evidence.v1",
        "official_contracts": official_contracts,
        "sandbox_reports": sandbox_reports,
    }


class ProviderContractActivationTest(unittest.TestCase):
    def test_activation_requires_a_clean_worktree_including_untracked_files(self) -> None:
        module = _module()
        module.validate_clean_worktree_status("")
        for status in (
            " M release/provider-contracts.json\n",
            "?? sandbox-report.json\n",
            "A  docs/ai-worklog.md\n",
        ):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "clean"):
                module.validate_clean_worktree_status(status)

    def test_committed_contract_is_the_only_runtime_authority(self) -> None:
        self.assertTrue(CONTRACT.exists(), "Provider contract authority is missing")
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "vowpic.provider-contracts.v1")
        self.assertEqual(
            payload["contracts"]["EVOLINK_SUBMISSION_RECONCILIATION"]["state"],
            "UNVERIFIED",
        )

        with patch.dict(
            os.environ,
            {
                "EVOLINK_SUBMISSION_RECONCILIATION": "VERIFIED",
                "EVOLINK_CONTRACT_STATE": "VERIFIED",
            },
        ):
            from app.core import provider_contracts

            self.assertEqual(
                provider_contracts.EVOLINK_SUBMISSION_RECONCILIATION.state.value,
                "UNVERIFIED",
            )

    def test_production_preflight_rejects_the_current_unverified_contracts(self) -> None:
        module = _readiness_module()
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "not VERIFIED"):
            module.validate_provider_readiness(payload)

    def test_activation_rejects_incomplete_wrong_stale_or_unsigned_evidence(self) -> None:
        module = _module()
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        source_sha = "c" * 40
        signing_key = b"provider-evidence-signing-key-32bytes"
        official = _official_contract(now)
        official_sha = hashlib.sha256(module.canonical_json_bytes(official)).hexdigest()
        report = _signed_report(
            module,
            now=now,
            tested_source_sha=source_sha,
            official_sha=official_sha,
            signing_key=signing_key,
        )

        invalid_inputs = (
            ({**official, "official_source_url": ""}, report, source_sha, signing_key),
            ({**official, "correlation_semantics": ""}, report, source_sha, signing_key),
            ({**official, "idempotency_semantics": ""}, report, source_sha, signing_key),
            (official, {**report, "tested_source_sha": "d" * 40}, source_sha, signing_key),
            (
                official,
                {**report, "produced_at": (now - timedelta(days=1)).isoformat()},
                source_sha,
                signing_key,
            ),
            (official, {**report, "signature": "hmac-sha256:" + "0" * 64}, source_sha, signing_key),
            (official, {**report, "submit_count": 2}, source_sha, signing_key),
            (official, report, source_sha, b"wrong-provider-evidence-key-32bytes"),
        )
        for official_input, report_input, expected_sha, key in invalid_inputs:
            with self.subTest(official=official_input, report=report_input), self.assertRaises(ValueError):
                module.activate_evolink_contract(
                    document,
                    official_contract=official_input,
                    sandbox_report=report_input,
                    expected_tested_source_sha=expected_sha,
                    signing_key=key,
                    approval_ref="provider-approval-123",
                    now=now,
                )

    def test_valid_activation_changes_only_evolink_and_preserves_creem_unverified(self) -> None:
        module = _module()
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        original = json.loads(json.dumps(document))
        now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
        source_sha = "c" * 40
        signing_key = b"provider-evidence-signing-key-32bytes"
        official = _official_contract(now)
        official_sha = hashlib.sha256(module.canonical_json_bytes(official)).hexdigest()
        report = _signed_report(
            module,
            now=now,
            tested_source_sha=source_sha,
            official_sha=official_sha,
            signing_key=signing_key,
        )

        activated = module.activate_evolink_contract(
            document,
            official_contract=official,
            sandbox_report=report,
            expected_tested_source_sha=source_sha,
            signing_key=signing_key,
            approval_ref="provider-approval-123",
            now=now,
        )

        evolink = activated["contracts"]["EVOLINK_SUBMISSION_RECONCILIATION"]
        self.assertEqual(evolink["state"], "VERIFIED")
        self.assertEqual(evolink["tested_source_sha"], source_sha)
        self.assertEqual(evolink["official_contract_sha256"], official_sha)
        for name, contract in activated["contracts"].items():
            if name.startswith("CREEM_"):
                self.assertEqual(contract, original["contracts"][name])
                self.assertEqual(contract["state"], "UNVERIFIED")

    def test_creem_activation_stays_not_run_without_an_official_refund_api(self) -> None:
        module = _module()
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        document["contracts"]["EVOLINK_SUBMISSION_RECONCILIATION"]["state"] = "VERIFIED"
        now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        source_sha = "c" * 40
        signing_key = b"provider-evidence-signing-key-32bytes"
        bundle = _creem_bundle(
            module,
            document=document,
            now=now,
            source_sha=source_sha,
            signing_key=signing_key,
        )
        before = json.loads(json.dumps(document))
        with self.assertRaisesRegex(
            ValueError,
            "refund creation API endpoint is not documented",
        ):
            module.activate_creem_contracts(
                document,
                evidence_bundle=bundle,
                expected_tested_source_sha=source_sha,
                signing_key=signing_key,
                approval_ref="provider-approval-123",
                now=now,
            )
        self.assertEqual(document, before)

    def test_creem_activation_cannot_bypass_unverified_evolink(self) -> None:
        module = _module()
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "VERIFIED Evolink"):
            module.activate_creem_contracts(
                document,
                evidence_bundle=_creem_bundle(
                    module,
                    document=document,
                    now=now,
                    source_sha="c" * 40,
                    signing_key=b"provider-evidence-signing-key-32bytes",
                ),
                expected_tested_source_sha="c" * 40,
                signing_key=b"provider-evidence-signing-key-32bytes",
                approval_ref="provider-approval-123",
                now=now,
            )

    def test_activation_commit_must_directly_follow_support_sha_and_touch_only_two_files(self) -> None:
        module = _module()
        support_sha = "a" * 40
        module.validate_activation_commit(
            tested_support_sha=support_sha,
            activation_parent_sha=support_sha,
            activation_source_sha="b" * 40,
            changed_paths={"release/provider-contracts.json", "docs/ai-worklog.md"},
        )
        for parent, paths in (
            ("c" * 40, {"release/provider-contracts.json", "docs/ai-worklog.md"}),
            (support_sha, {"release/provider-contracts.json"}),
            (support_sha, {"release/provider-contracts.json", "docs/ai-worklog.md", "backend/app/main.py"}),
        ):
            with self.subTest(parent=parent, paths=paths), self.assertRaises(ValueError):
                module.validate_activation_commit(
                    tested_support_sha=support_sha,
                    activation_parent_sha=parent,
                    activation_source_sha="b" * 40,
                    changed_paths=paths,
                )


if __name__ == "__main__":
    unittest.main()
