"""Versioned release gate registry and fail-closed aggregation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "release"
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _case(case_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": case_id,
        "layer": "preview",
        "runtime_scope": "preview",
        "profiles": ["stage5_foundation", "release"],
        "mandatory": True,
        "timeout_seconds": 600,
        "freshness_seconds": 3600,
        "report_schema": "vowpic.gate-evidence.v1",
        "na_eligible": False,
        "capability": None,
    }
    payload.update(overrides)
    return payload


def _contract(*cases: dict[str, object]) -> dict[str, object]:
    case_ids = [str(case["id"]) for case in cases]
    return {
        "schema": "vowpic.gates.v1",
        "statuses": ["PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"],
        "cases": list(cases),
        "profiles": {
            "stage5_foundation": case_ids,
            "release": case_ids,
        },
    }


def _evidence(
    case_id: str,
    *,
    status: str = "PASS",
    source_sha: str = "a" * 40,
    runtime_bundle_id: str = "rtb_" + "b" * 64,
    contract_sha256: str = "c" * 64,
    produced_at: datetime = NOW,
    execution: str = "completed",
    capability_enabled: bool = False,
) -> dict[str, object]:
    return {
        "schema": "vowpic.gate-evidence.v1",
        "case_id": case_id,
        "status": status,
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "gate_contract_sha256": contract_sha256,
        "produced_at": produced_at.isoformat(),
        "execution": execution,
        "test_count": 1,
        "capability_enabled": capability_enabled,
    }


class ReleaseAggregationTest(unittest.TestCase):
    def _aggregate(
        self,
        contract: dict[str, object],
        evidence: list[dict[str, object]],
        *,
        profile: str = "stage5_foundation",
        enabled_capabilities: set[str] | None = None,
    ):
        from scripts.release.aggregate_gates import aggregate

        return aggregate(
            contract,
            evidence,
            profile=profile,
            expected_source_sha="a" * 40,
            expected_runtime_bundle_id="rtb_" + "b" * 64,
            expected_contract_sha256="c" * 64,
            now=NOW,
            enabled_capabilities=enabled_capabilities or set(),
        )

    def test_mandatory_case_set_must_match_exactly(self) -> None:
        contract = _contract(_case("auth"), _case("billing"), _case("generation"))
        result = self._aggregate(contract, [_evidence("auth"), _evidence("billing")])
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.missing_case_ids, ["generation"])

        unexpected = self._aggregate(
            contract,
            [_evidence("auth"), _evidence("billing"), _evidence("generation"), _evidence("extra")],
        )
        self.assertEqual(unexpected.unexpected_case_ids, ["extra"])
        self.assertEqual(unexpected.status, "FAIL")

        duplicate = self._aggregate(
            contract,
            [_evidence("auth"), _evidence("auth"), _evidence("billing"), _evidence("generation")],
        )
        self.assertEqual(duplicate.duplicate_case_ids, ["auth"])
        self.assertEqual(duplicate.status, "FAIL")

    def test_not_run_timeout_cancel_skip_and_zero_tests_never_pass(self) -> None:
        from scripts.release.aggregate_gates import aggregate_one

        case = _case("auth")
        for status, execution, test_count in (
            ("NOT_RUN", "completed", 1),
            ("FAIL", "completed", 1),
            ("PASS", "timed_out", 1),
            ("PASS", "cancelled", 1),
            ("PASS", "skipped", 1),
            ("PASS", "completed", 0),
        ):
            with self.subTest(status=status, execution=execution, test_count=test_count):
                evidence = _evidence("auth", status=status, execution=execution)
                evidence["test_count"] = test_count
                self.assertEqual(
                    aggregate_one(case, evidence, enabled_capabilities=set()),
                    "FAIL",
                )

    def test_not_applicable_requires_allowlist_and_disabled_capability(self) -> None:
        from scripts.release.aggregate_gates import aggregate_one

        eligible = _case("partner", na_eligible=True, capability="partner_invite")
        evidence = _evidence("partner", status="NOT_APPLICABLE")
        self.assertEqual(aggregate_one(eligible, evidence, enabled_capabilities=set()), "PASS")
        self.assertEqual(
            aggregate_one(eligible, evidence, enabled_capabilities={"partner_invite"}),
            "FAIL",
        )
        ineligible = _case("billing", na_eligible=False)
        self.assertEqual(aggregate_one(ineligible, evidence, enabled_capabilities=set()), "FAIL")

    def test_freshness_source_bundle_and_contract_checksum_are_exact(self) -> None:
        contract = _contract(_case("auth", freshness_seconds=300))
        for mutation in (
            {"produced_at": (NOW - timedelta(seconds=301)).isoformat()},
            {"source_sha": "d" * 40},
            {"runtime_bundle_id": "rtb_" + "e" * 64},
            {"gate_contract_sha256": "f" * 64},
        ):
            evidence = _evidence("auth")
            evidence.update(mutation)
            with self.subTest(mutation=mutation):
                result = self._aggregate(contract, [evidence])
                self.assertEqual(result.status, "FAIL")
                self.assertIn("auth", result.invalid_case_ids)

    def test_runtime_scopes_require_an_exact_complete_binding_map(self) -> None:
        from scripts.release.aggregate_gates import GateContractError, aggregate

        contract = _contract(
            _case("identity", runtime_scope="preview_identity"),
            _case("commercial", runtime_scope="preview_commercial"),
        )
        evidence = [
            _evidence("identity", runtime_bundle_id="rtb_" + "1" * 64),
            _evidence("commercial", runtime_bundle_id="rtb_" + "2" * 64),
        ]
        result = aggregate(
            contract,
            evidence,
            profile="stage5_foundation",
            expected_source_sha="a" * 40,
            expected_runtime_bindings={
                "preview_identity": "rtb_" + "1" * 64,
                "preview_commercial": "rtb_" + "2" * 64,
            },
            expected_contract_sha256="c" * 64,
            now=NOW,
        )
        self.assertEqual(result.status, "PASS")
        with self.assertRaisesRegex(GateContractError, "runtime scope bindings"):
            aggregate(
                contract,
                evidence,
                profile="stage5_foundation",
                expected_source_sha="a" * 40,
                expected_runtime_bindings={"preview_identity": "rtb_" + "1" * 64},
                expected_contract_sha256="c" * 64,
                now=NOW,
            )
        wrong = aggregate(
            contract,
            evidence,
            profile="stage5_foundation",
            expected_source_sha="a" * 40,
            expected_runtime_bindings={
                "preview_identity": "rtb_" + "1" * 64,
                "preview_commercial": "rtb_" + "3" * 64,
            },
            expected_contract_sha256="c" * 64,
            now=NOW,
        )
        self.assertEqual(wrong.status, "FAIL")
        self.assertEqual(wrong.invalid_case_ids, ["commercial"])

    def test_stage5_readiness_never_implies_release_readiness(self) -> None:
        stage5 = _case("stage5", profiles=["stage5_foundation", "release"])
        stage6 = _case("stage6", profiles=["release"])
        contract = _contract(stage5, stage6)
        contract["profiles"] = {
            "stage5_foundation": ["stage5"],
            "release": ["stage5", "stage6"],
        }
        stage5_result = self._aggregate(contract, [_evidence("stage5")])
        self.assertEqual(stage5_result.status, "PASS")
        self.assertTrue(stage5_result.stage5_foundation_ready)
        self.assertFalse(stage5_result.release_ready)

        release_result = self._aggregate(
            contract,
            [_evidence("stage5"), _evidence("stage6", status="NOT_RUN")],
            profile="release",
        )
        self.assertEqual(release_result.status, "FAIL")
        self.assertFalse(release_result.release_ready)

    def test_real_registry_stage5_can_pass_while_explicit_stage6_not_run_keeps_release_closed(self) -> None:
        from scripts.release.aggregate_gates import aggregate

        contract_path = RELEASE / "gates.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract_sha = hashlib.sha256(contract_path.read_bytes()).hexdigest()
        stage5_ids = set(contract["profiles"]["stage5_foundation"])
        release_ids = contract["profiles"]["release"]
        evidence = [
            _evidence(
                case_id,
                status="PASS" if case_id in stage5_ids else "NOT_RUN",
                contract_sha256=contract_sha,
            )
            for case_id in release_ids
        ]
        result = aggregate(
            contract,
            evidence,
            profile="release",
            expected_source_sha="a" * 40,
            expected_runtime_bindings={
                scope: "rtb_" + "b" * 64
                for scope in {case["runtime_scope"] for case in contract["cases"]}
            },
            expected_contract_sha256=contract_sha,
            now=NOW,
        )
        self.assertEqual(result.status, "FAIL")
        self.assertTrue(result.stage5_foundation_ready)
        self.assertFalse(result.release_ready)
        self.assertEqual(
            set(result.invalid_case_ids),
            set(release_ids) - stage5_ids,
        )

    def test_generation_affecting_or_unknown_changes_force_full_quality(self) -> None:
        from scripts.release.aggregate_gates import evaluate_change_impact

        impact = json.loads((RELEASE / "change-impact.json").read_text(encoding="utf-8"))
        for paths in (
            ["backend/app/services/evolink_service.py"],
            ["release/provider-capabilities.json"],
            ["frontend/src/pages/index/index.vue", None],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(evaluate_change_impact(impact, paths), "FULL_QUALITY")
        self.assertEqual(evaluate_change_impact(impact, ["docs/README.md"]), "CANARY_ONLY")


class ReleaseRegistryTest(unittest.TestCase):
    def test_gate_registry_is_versioned_closed_and_profile_complete(self) -> None:
        contract = json.loads((RELEASE / "gates.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema"], "vowpic.gates.v1")
        self.assertEqual(
            contract["statuses"],
            ["PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"],
        )
        case_ids = [case["id"] for case in contract["cases"]]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertGreaterEqual(len(case_ids), 20)
        by_id = {case["id"]: case for case in contract["cases"]}
        self.assertEqual(
            {case["runtime_scope"] for case in contract["cases"]},
            {"pr", "preview_identity", "preview_commercial", "stage5_composite", "production_7a", "production_7b"},
        )
        for profile, profile_case_ids in contract["profiles"].items():
            with self.subTest(profile=profile):
                self.assertEqual(len(profile_case_ids), len(set(profile_case_ids)))
                self.assertTrue(set(profile_case_ids).issubset(by_id))
                self.assertEqual(
                    set(profile_case_ids),
                    {case_id for case_id, case in by_id.items() if profile in case["profiles"]},
                )
        self.assertTrue(set(contract["profiles"]["stage5_foundation"]).issubset(
            contract["profiles"]["release"]
        ))
        self.assertNotIn("preview_creem_test_contract", contract["profiles"]["stage5_foundation"])
        self.assertIn("preview_creem_test_contract", contract["profiles"]["release"])

    def test_quality_severity_activation_and_runtime_contracts_are_fixed(self) -> None:
        quality_cases = json.loads((RELEASE / "quality-cases.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [case["id"] for case in quality_cases["cases"]],
            [
                "single_template",
                "single_text",
                "single_outdoor_text",
                "local_couple",
                "golden_anniversary",
                "partner_invite_remote_couple",
            ],
        )
        rubric = json.loads((RELEASE / "quality-rubric.json").read_text(encoding="utf-8"))
        self.assertEqual(
            rubric["dimensions"],
            ["identity", "composition", "attire_style", "naturalness_exposure"],
        )
        self.assertEqual(rubric["average_minimum"], 4.0)
        self.assertEqual(rubric["dimension_minimum"], 3)
        self.assertEqual(rubric["hard_defects"], [
            "identity",
            "safety",
            "subject_count",
            "technical",
        ])

        severity = json.loads((RELEASE / "severity-contract.json").read_text(encoding="utf-8"))
        self.assertEqual(severity["default_unknown_production"], "P1")
        for category in ("unauthorized_access", "privacy", "key_exposure", "data_loss", "double_charge"):
            self.assertEqual(severity["categories"][category], "P0")

        activation = json.loads((RELEASE / "activation-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(set(activation["flag_order"]), set(activation["target_snapshot"]))
        self.assertTrue(all(state == "ON" for state in activation["target_snapshot"].values()))
        self.assertTrue(all(state == "OFF" for state in activation["rollback_snapshot"].values()))

        runtime = json.loads(
            (ROOT / "backend/contracts/runtime-contracts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(runtime["schema_revision"], "20260710_0020")
        self.assertEqual(runtime["job_payload_version"], "generation-job.v1")
        for relative_path, expected_hash in runtime["source_sha256"].items():
            actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
            with self.subTest(relative_path=relative_path):
                self.assertEqual(expected_hash, actual)


if __name__ == "__main__":
    unittest.main()
