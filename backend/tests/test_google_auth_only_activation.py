"""Fail-closed contracts for the production GOOGLE_AUTH_ONLY release type."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _path_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoogleAuthOnlyActivationTest(unittest.IsolatedAsyncioTestCase):
    def _assert_production_clean_off_contract(self, source: str) -> None:
        workflow = yaml.safe_load(source)
        gate_jobs = (
            "authorize-google-deploy",
            "deploy-google-runtime",
            "google-auth-only",
            "rollback-google-deploy",
        )
        gate_blocks = []
        for job_name in gate_jobs:
            matches = [
                step["run"]
                for step in workflow["jobs"][job_name]["steps"]
                if isinstance(step, dict)
                and isinstance(step.get("run"), str)
                and "SELECT capability, state" in step["run"]
                and "database_off = (" in step["run"]
            ]
            self.assertEqual(len(matches), 1, job_name)
            gate_blocks.append((job_name, matches[0]))

        expected_select_fields = (
            "capability",
            "state",
            "deployment_id",
            "runtime_bundle_id",
            "worker_image_digest",
            "release_activation_id",
            "target_manifest_sha256",
            "expires_at",
            "cohort_user_ids",
            "verified_identity_hashes",
        )
        expected_bound_fields = (
            "deployment_id",
            "runtime_bundle_id",
            "worker_image_digest",
            "release_activation_id",
            "target_manifest_sha256",
            "expires_at",
        )
        clean_row = {
            "state": "OFF",
            **{field: None for field in expected_bound_fields},
            "cohort_user_ids": [],
            "verified_identity_hashes": [],
        }
        expected_capabilities = {
            "google_auth",
            "authenticated_upload",
            "generation",
            "credit_pack_checkout",
            "subscription_billing",
            "private_download",
            "partner_invite",
        }
        cases = (
            ("clean", {}, True),
            ("dirty_worker", {"worker_image_digest": "sha256:" + "a" * 64}, False),
            ("dirty_cohort", {"cohort_user_ids": ["user-1"]}, False),
            ("dirty_verified", {"verified_identity_hashes": ["b" * 64]}, False),
        )

        def parenthesized_assignment(block: str, marker: str) -> str:
            start = block.index(marker)
            opening = block.index("(", start)
            depth = 0
            for index in range(opening, len(block)):
                if block[index] == "(":
                    depth += 1
                elif block[index] == ")":
                    depth -= 1
                    if depth == 0:
                        line_end = block.find("\n", index + 1)
                        return block[start : line_end if line_end >= 0 else len(block)]
            raise AssertionError(f"unterminated assignment: {marker}")

        for job_name, block in gate_blocks:
            select_match = re.search(
                r"SELECT capability, state,(.*?)FROM ops_feature_flags",
                block,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(select_match, job_name)
            select_fields = (
                "capability",
                "state",
                *re.findall(r"[a-z][a-z0-9_]+", select_match.group(1)),
            )
            self.assertEqual(select_fields, expected_select_fields, job_name)

            contract_start = block.index("bound = (")
            database_start = block.index("database_off = (", contract_start)
            contract_module = ast.parse(block[contract_start:database_start])
            self.assertEqual(len(contract_module.body), 2, job_name)
            self.assertIsInstance(contract_module.body[0], ast.Assign, job_name)
            self.assertIsInstance(contract_module.body[1], ast.FunctionDef, job_name)
            namespace = {"all": all, "list": list}
            exec(compile(contract_module, f"<{job_name}-clean-off>", "exec"), namespace)
            self.assertEqual(namespace["bound"], expected_bound_fields, job_name)

            database_assignment = ast.parse(
                parenthesized_assignment(block, "database_off = (")
            ).body[0]
            self.assertIsInstance(database_assignment, ast.Assign, job_name)
            self.assertIsInstance(database_assignment.value, ast.BoolOp, job_name)
            self.assertIsInstance(database_assignment.value.op, ast.And, job_name)
            self.assertFalse(
                any(isinstance(node, ast.Or) for node in ast.walk(database_assignment.value)),
                job_name,
            )
            actual_collection = "rows" if job_name == "google-auth-only" else "flags"
            actual_gate_calls = []
            for node in ast.walk(database_assignment.value):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "all"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.GeneratorExp)
                ):
                    generator = node.args[0]
                    comprehension = generator.generators[0]
                    if (
                        isinstance(generator.elt, ast.Call)
                        and isinstance(generator.elt.func, ast.Name)
                        and generator.elt.func.id == "clean_off"
                        and len(generator.elt.args) == 1
                        and isinstance(generator.elt.args[0], ast.Name)
                        and len(generator.generators) == 1
                        and isinstance(comprehension.target, ast.Name)
                        and generator.elt.args[0].id == comprehension.target.id
                        and isinstance(comprehension.iter, ast.Name)
                        and comprehension.iter.id == actual_collection
                    ):
                        actual_gate_calls.append(node)
            self.assertEqual(len(actual_gate_calls), 1, job_name)

            for case_name, changes, expected in cases:
                row = {**clean_row, **changes}
                actual = namespace["clean_off"](row)
                self.assertIs(actual, expected, f"{job_name}:{case_name}")

                inventory = [
                    {**clean_row, "capability": capability}
                    for capability in sorted(expected_capabilities)
                ]
                inventory[0] = {**inventory[0], **changes}
                gate_namespace = {
                    **namespace,
                    "expected": expected_capabilities,
                    actual_collection: inventory,
                }
                exec(
                    compile(
                        ast.Module(body=[database_assignment], type_ignores=[]),
                        f"<{job_name}-database-off>",
                        "exec",
                    ),
                    gate_namespace,
                )
                self.assertIs(
                    gate_namespace["database_off"],
                    expected,
                    f"{job_name}:{case_name}:actual_database_off",
                )

    def test_all_four_production_clean_off_gates_share_the_complete_contract(self) -> None:
        source = (ROOT / ".github/workflows/production-google-auth-only.yml").read_text(
            encoding="utf-8"
        )
        self._assert_production_clean_off_contract(source)

        gate_boundaries = (
            ("authorize-google-deploy", "deploy-google-runtime", "flags"),
            ("deploy-google-runtime", "google-auth-only", "flags"),
            ("google-auth-only", "rollback-google-deploy", "rows"),
            ("rollback-google-deploy", "expired-lease-watchdog", "flags"),
        )
        clean_off_body = """          def clean_off(row):
              return (
                  row["state"] == "OFF"
                  and all(row[key] is None for key in bound)
                  and not list(row["cohort_user_ids"] or [])
                  and not list(row["verified_identity_hashes"] or [])
              )"""
        constant_clean_row = (
            '{"state": "OFF", "deployment_id": None, "runtime_bundle_id": None, '
            '"worker_image_digest": None, "release_activation_id": None, '
            '"target_manifest_sha256": None, "expires_at": None, '
            '"cohort_user_ids": [], "verified_identity_hashes": []}'
        )

        def append_or_true(region: str) -> str:
            marker = "database_off = ("
            start = region.index(marker)
            opening = region.index("(", start)
            depth = 0
            for index in range(opening, len(region)):
                if region[index] == "(":
                    depth += 1
                elif region[index] == ")":
                    depth -= 1
                    if depth == 0:
                        return region[: index + 1] + " or True" + region[index + 1 :]
            raise AssertionError("unterminated database_off assignment")

        for job_name, next_job, collection in gate_boundaries:
            start = source.index(f"  {job_name}:\n")
            end = source.index(f"  {next_job}:\n", start)
            region = source[start:end]

            invocation = f"and all(clean_off(row) for row in {collection})"
            self.assertEqual(region.count(invocation), 1, job_name)
            detached_region = region.replace(invocation, "and True", 1)
            detached_source = source[:start] + detached_region + source[end:]
            with self.subTest(gate=job_name, mutation="detached_invocation"):
                with self.assertRaises(AssertionError):
                    self._assert_production_clean_off_contract(detached_source)

            self.assertEqual(region.count(clean_off_body), 1, job_name)
            permissive_region = region.replace(
                clean_off_body,
                "          def clean_off(row):\n              return True",
                1,
            )
            permissive_source = source[:start] + permissive_region + source[end:]
            with self.subTest(gate=job_name, mutation="permissive_function"):
                with self.assertRaises(AssertionError):
                    self._assert_production_clean_off_contract(permissive_source)

            or_true_region = append_or_true(region)
            or_true_source = source[:start] + or_true_region + source[end:]
            with self.subTest(gate=job_name, mutation="or_true"):
                with self.assertRaises(AssertionError):
                    self._assert_production_clean_off_contract(or_true_source)

            constant_invocation = (
                f"and all(clean_off({constant_clean_row}) for row in {collection})"
            )
            constant_region = region.replace(invocation, constant_invocation, 1)
            self.assertNotEqual(constant_region, region, job_name)
            constant_source = source[:start] + constant_region + source[end:]
            with self.subTest(gate=job_name, mutation="constant_clean_row"):
                with self.assertRaises(AssertionError):
                    self._assert_production_clean_off_contract(constant_source)

    def test_isolated_google_deploy_is_exactly_gated_and_rollback_bound(self) -> None:
        source = (ROOT / ".github/workflows/production-google-auth-only.yml").read_text(
            encoding="utf-8"
        )
        workflow = yaml.safe_load(source)
        dispatch_inputs = workflow[True]["workflow_dispatch"]["inputs"]
        for name in (
            "ci_run_id",
            "ci_run_attempt",
            "privacy_run_id",
            "privacy_run_attempt",
            "preview_readiness_run_id",
            "preview_readiness_run_attempt",
        ):
            self.assertTrue(dispatch_inputs[name]["required"], name)
        jobs = workflow["jobs"]
        self.assertEqual(jobs["deploy-google-runtime"]["needs"], "authorize-google-deploy")
        self.assertEqual(jobs["google-auth-only"]["needs"], "deploy-google-runtime")
        self.assertEqual(
            jobs["rollback-google-deploy"]["needs"],
            ["authorize-google-deploy", "deploy-google-runtime", "google-auth-only"],
        )
        authorize = source[
            source.index("  authorize-google-deploy:\n") :
            source.index("  deploy-google-runtime:\n")
        ]
        deploy = source[
            source.index("  deploy-google-runtime:\n") : source.index("  google-auth-only:\n")
        ]
        rollback = source[
            source.index("  rollback-google-deploy:\n") :
            source.index("  expired-lease-watchdog:\n")
        ]
        self.assertLess(
            authorize.index("verify_github_workflow_run.py"),
            authorize.index("google-deploy-baseline.json"),
        )
        self.assertIn("PRODUCTION_READ_ONLY_DATABASE_URL", authorize)
        self.assertIn("database_and_public_all_off", authorize)
        self.assertIn("vowpic.preview-google-handoff-readiness.v2", authorize)
        self.assertIn("supabase_first_hop_redirect_validated", authorize)
        self.assertIn("browser_google_requests_observed", authorize)
        self.assertIn("browser_google_responses_observed", authorize)
        self.assertIn("google_redirect_followed", authorize)
        self.assertIn("real_google_identity_proof", authorize)
        self.assertIn("deferred_to_production_google_only", authorize)
        self.assertIn("deploy --prebuilt --prod --skip-domain", deploy)
        self.assertIn("--expected-release-role COMMERCIAL_7A", deploy)
        self.assertIn('"commercial_activation_created": False', deploy)
        self.assertIn('"database_all_off": database_off', deploy)
        self.assertNotIn("register_bundle.py", deploy)
        self.assertNotIn("apply_activation_plan.py", deploy)
        self.assertNotIn("production-release.yml", source)
        self.assertIn("emergency_disable", rollback)
        self.assertIn('"active_google_activations": active_activations', rollback)
        self.assertIn('"active_unused_bindings": active_unused_bindings', rollback)
        self.assertIn('"active_acceptance_sessions": active_sessions', rollback)
        self.assertIn('"$VERCEL_CLI" rollback "$BASELINE_DEPLOYMENT_ID"', rollback)
        self.assertIn('"$VERCEL_CLI" remove "$deployment_id"', rollback)

    def test_0021_adds_only_the_production_google_auth_activation_kind(self) -> None:
        source = (
            ROOT / "backend/alembic/versions/20260710_0021_google_auth_only_activation.py"
        ).read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0021"', source)
        self.assertIn('down_revision = "20260710_0020"', source)
        self.assertIn("GOOGLE_AUTH_ONLY", source)
        environment_expression = source.split("_ENVIRONMENT_KINDS_WITH_GOOGLE_AUTH_ONLY = (", 1)[1].split(")\n_ORIGINAL_KINDS", 1)[0]
        self.assertIn("environment = 'production'", environment_expression)
        self.assertNotIn(
            "GOOGLE_AUTH_ONLY",
            environment_expression.split("environment = 'preview'", 1)[1].split("OR", 1)[0],
        )

    async def test_service_rejects_every_non_google_capability_for_google_auth_only(self) -> None:
        flags = importlib.import_module("app.core.feature_flags")
        service = importlib.import_module("app.services.feature_flag_service")
        activation = SimpleNamespace(
            id=uuid4(),
            environment="production",
            kind="GOOGLE_AUTH_ONLY",
            phase="ACCEPTANCE_READY",
            runtime_bundle_id="rtb_" + "a" * 64,
            api_deployment_id="dpl_target",
        )
        result = SimpleNamespace(scalar_one_or_none=lambda: activation)
        db = AsyncMock()
        db.execute.return_value = result
        accepted = await service._validate_activation_for_state(
            db,
            activation_id=activation.id,
            environment="production",
            capability=flags.Capability.GOOGLE_AUTH,
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_" + "a" * 64,
        )
        self.assertIs(accepted, activation)
        for capability in flags.Capability:
            if capability is flags.Capability.GOOGLE_AUTH:
                continue
            with self.subTest(capability=capability.value), self.assertRaisesRegex(
                ValueError, "cannot authorize"
            ):
                await service._validate_activation_for_state(
                    db,
                    activation_id=activation.id,
                    environment="production",
                    capability=capability,
                    deployment_id="dpl_target",
                    runtime_bundle_id="rtb_" + "a" * 64,
                )

    def test_google_auth_only_plan_cannot_select_a_commercial_phase(self) -> None:
        module = _path_module(
            "google_auth_only_plan",
            ROOT / "scripts/release/apply_activation_plan.py",
        )
        plan = json.loads((ROOT / "release/activation-plan.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "only Google auth"):
            module.apply_phase(
                "postgresql://unused",
                phase="formal-cohort",
                plan=plan,
                approval="approved",
                kind="GOOGLE_AUTH_ONLY",
                deployment_id="dpl_target",
                source_sha="a" * 40,
                binding_report=None,
            )

    def test_runtime_registration_is_exactly_bound_to_schema_0021(self) -> None:
        module = _path_module(
            "manage_google_auth_only",
            ROOT / "scripts/release/manage_google_auth_only_activation.py",
        )
        source_sha = "a" * 40
        coordinates = module.validate_runtime_report(
            {
                "source_sha": source_sha,
                "runtime_bundle_id": "rtb_" + "b" * 64,
                "deployment_id": "dpl_target",
                "release_role": "COMMERCIAL_7A",
                "runtime_environment": "production",
                "schema_revision": "20260710_0021",
            },
            source_sha=source_sha,
            base_url="https://www.vowpic.com",
        )
        manifest = module.activation_manifest(coordinates)
        self.assertEqual(manifest["kind"], "GOOGLE_AUTH_ONLY")
        self.assertEqual(manifest["schema_revision"], "20260710_0021")
        with self.assertRaisesRegex(ValueError, "schema"):
            module.validate_runtime_report(
                {**coordinates, "schema_revision": "20260710_0020"},
                source_sha=source_sha,
                base_url="https://www.vowpic.com",
            )

    def test_workflow_has_no_generation_payment_or_commercial_activation_path(self) -> None:
        path = ROOT / ".github/workflows/production-google-auth-only.yml"
        full_source = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(full_source)
        source = full_source[
            full_source.index("  google-auth-only:\n") :
            full_source.index("  rollback-google-deploy:\n")
        ]
        self.assertIn("workflow_dispatch", workflow[True])
        self.assertEqual(workflow[True]["schedule"][0]["cron"], "*/5 * * * *")
        dispatch_inputs = workflow[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(dispatch_inputs["operator_ready"]["type"], "boolean")
        self.assertTrue(dispatch_inputs["operator_ready"]["required"])
        self.assertEqual(workflow["concurrency"]["group"], "vowpic-production-release")
        self.assertEqual(workflow["jobs"]["google-auth-only"]["environment"], "production")
        self.assertEqual(workflow["jobs"]["expired-lease-watchdog"]["environment"], "production")
        self.assertIn(
            "manage_google_auth_only_activation.py reap-expired",
            full_source,
        )
        self.assertIn(
            "inputs.operator_ready == true",
            workflow["jobs"]["google-auth-only"]["if"],
        )
        upload = next(
            step
            for step in workflow["jobs"]["google-auth-only"]["steps"]
            if str(step.get("uses") or "").startswith("actions/upload-artifact@")
        )
        uploaded_paths = str(upload["with"]["path"])
        self.assertNotIn("google-requested-subjects.json", uploaded_paths)
        self.assertNotIn("google-resolved-subjects.json", uploaded_paths)
        self.assertIn("google-subject-resolution.json", uploaded_paths)
        self.assertNotIn("PRODUCTION_GOOGLE_SUBJECTS_B64", source)
        self.assertNotIn("google-requested-subjects.json", source)
        self.assertLess(
            source.index("resolve_google_acceptance_subjects.py"),
            source.index("manage_google_auth_only_activation.py reserve"),
        )
        self.assertLess(
            source.index("trap cleanup EXIT"),
            source.index("manage_google_auth_only_activation.py reserve"),
        )
        self.assertLess(
            source.index('activation_reserved=0'),
            source.index("trap cleanup EXIT"),
        )
        for required in (
            "--kind GOOGLE_AUTH_ONLY",
            "--phase google-auth-only",
            "--phase emergency-off",
            "wait_for_google_auth_sessions.py",
            "--hmac-key-env ACCEPTANCE_IDENTITY_HMAC_KEY",
            "cleanup_google_auth_sessions.py",
            "--timeout-seconds 1800",
            "google-interactive-acceptance.json",
            '"active_acceptance_sessions": active_sessions',
            "google-only-final-state.json",
            "manage_google_auth_only_activation.py complete",
            "resolve_google_acceptance_subjects.py",
            "google-subject-resolution.json",
            '--subjects-file "$RUNNER_TEMP/google-resolved-subjects.json"',
            "--project-ref-env SUPABASE_PROJECT_REF",
            "--token-env SUPABASE_AUTH_CONFIG_TOKEN",
            "--primary-email-env PRODUCTION_GOOGLE_EMAIL",
            "--partner-email-env PRODUCTION_GOOGLE_PARTNER_EMAIL",
            "google-negative-account.json",
            "google_session_exchange stage=authorization_unavailable",
            "protected_bindings_still_unconsumed",
            '"$VERCEL_CLI" logs "$GOOGLE_DEPLOYMENT_ID"',
            '--status-code 403',
        ):
            self.assertIn(required, source)
        self.assertLess(
            source.index('"schema": "vowpic.google-auth-only-negative-account.v1"'),
            source.index("python scripts/release/wait_for_google_auth_sessions.py"),
        )
        for forbidden in (
            "PRODUCTION_GOOGLE_STORAGE_STATE_BASE64",
            "PRODUCTION_GOOGLE_PARTNER_STORAGE_STATE_BASE64",
            "ACCEPTANCE_EVIDENCE_SIGNING_KEY",
            "npm --prefix frontend run test:e2e",
            "images/generations",
            "orders/create",
            "payments/checkout",
            "CREEM_",
            "EVOLINK_",
            "--phase staged-user-cohort",
            "--phase formal-cohort",
            "vercel deploy",
            "vercel promote",
        ):
            self.assertNotIn(forbidden, source)

    def test_protected_privacy_job_is_trusted_main_sha_only_and_source_free(self) -> None:
        source = (
            ROOT / ".github/workflows/google-auth-protected-privacy.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("push:\n    branches: [main]", source)
        self.assertNotIn("pull_request:", source)
        self.assertNotIn("workflow_dispatch:", source)
        workflow = yaml.safe_load(source)
        job = workflow["jobs"]["google-auth-protected-privacy"]
        condition = str(job["if"])
        self.assertIn("github.event_name == 'push'", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertIn("github.event.repository.fork == false", condition)
        self.assertEqual(job["environment"], "production")
        self.assertEqual(job["permissions"], {"contents": "read"})
        checkout = next(
            step for step in job["steps"] if str(step.get("uses") or "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])
        gate = next(
            step for step in job["steps"] if step.get("name") == "Run the non-echo Google auth privacy boundary"
        )
        run = str(gate["run"])
        self.assertIn("verify_google_auth_privacy_boundary.py", run)
        self.assertIn('> "$privacy_log" 2>&1', run)
        self.assertNotIn("${{ secrets.", run)
        for name in (
            "PRODUCTION_GOOGLE_EMAIL",
            "PRODUCTION_GOOGLE_PARTNER_EMAIL",
            "SUPABASE_AUTH_CONFIG_TOKEN",
            "VERCEL_TOKEN",
        ):
            self.assertEqual(gate["env"][name], "${{ secrets." + name + " }}")
        upload = next(
            step for step in job["steps"] if str(step.get("uses") or "").startswith("actions/upload-artifact@")
        )
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/google-auth-privacy.json")
        self.assertNotIn("privacy-tests.log", str(upload["with"]))

    def test_privacy_scanner_detects_leaks_without_emitting_protected_values(self) -> None:
        module = _path_module(
            "verify_google_auth_privacy_boundary",
            ROOT / "scripts/release/verify_google_auth_privacy_boundary.py",
        )
        protected = (
            b"primary-protected@example.invalid",
            b"partner-protected@example.invalid",
            b"supabase-protected-value",
            b"vercel-protected-value",
        )
        report = module.build_report(
            source_sha="a" * 40,
            sources=(("checkout", b"source-free candidate"), ("test-output", b"OK")),
            protected_values=protected,
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["protected_values_zero_hits"])
        self.assertTrue(report["synthetic_canaries_detected"])
        self.assertTrue(report["sanitized_outputs_zero_hits"])
        serialized = json.dumps(report, sort_keys=True).encode()
        self.assertTrue(all(value not in serialized for value in protected))
        with self.assertRaisesRegex(ValueError, "reached release evidence"):
            module.build_report(
                source_sha="a" * 40,
                sources=(("captured-output", b"prefix " + protected[2] + b" suffix"),),
                protected_values=protected,
            )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "privacy.json"
            module.write_create_once(output, report)
            with self.assertRaises(FileExistsError):
                module.write_create_once(output, report)

    def test_real_postgres_gate_forces_runner_death_before_watchdog_takeover(self) -> None:
        source = (
            ROOT / "backend/tests/integration/test_google_auth_acceptance_fence.py"
        ).read_text(encoding="utf-8")
        for required in (
            "subprocess.Popen",
            "process.kill()",
            "reserve_activation(",
            "manage.reap_expired_activations(",
            'self.assertEqual(second["reaped_count"], 0)',
            "session.revoked_at IS NULL",
            "refresh.status='ACTIVE'",
            "consumed_at IS NULL AND revoked_at IS NULL",
        ):
            self.assertIn(required, source)
        self.assertIn(
            "test_watchdog_never_completes_activation_when_session_cleanup_is_not_zero",
            (ROOT / "backend/tests/test_google_auth_only_activation.py").read_text(
                encoding="utf-8"
            ),
        )

    def test_runtime_admission_and_every_cleanup_share_one_global_fence(self) -> None:
        expected = "vowpic-production-capability-activation"
        paths = (
            ROOT / "backend/app/services/feature_flag_service.py",
            ROOT / "scripts/release/apply_activation_plan.py",
            ROOT / "scripts/release/manage_google_auth_only_activation.py",
            ROOT / "scripts/release/cleanup_acceptance_bindings.py",
            ROOT / "scripts/release/cleanup_google_auth_sessions.py",
        )
        for path in paths:
            with self.subTest(path=path.name):
                self.assertIn(expected, path.read_text(encoding="utf-8"))
        manage_source = paths[2].read_text(encoding="utf-8")
        self.assertIn("reservation_expires_at", manage_source)
        self.assertIn("INTERVAL '30 minutes'", manage_source)

    def test_google_subject_resolution_accepts_current_supabase_or_provider_subjects(self) -> None:
        module = _path_module(
            "resolve_google_acceptance_subjects",
            ROOT / "scripts/release/resolve_google_acceptance_subjects.py",
        )
        resolver_source = (
            ROOT / "scripts/release/resolve_google_acceptance_subjects.py"
        ).read_text(encoding="utf-8")
        self.assertIn("auth.identities", resolver_source)
        self.assertIn("provider = 'google'", resolver_source)
        self.assertIn("/database/query/read-only", resolver_source)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", resolver_source)
        self.assertNotIn('"requested": requested', resolver_source)
        self.assertNotIn('"resolved": resolved', resolver_source)
        first = "11111111-1111-1111-1111-111111111111"
        second = "22222222-2222-2222-2222-222222222222"
        rows = [
            {
                "provider": "google",
                "provider_id": "google-native-a",
                "identity_sub": "google-native-a",
                "user_id": first,
            },
            {
                "provider": "google",
                "provider_id": "google-native-b",
                "identity_sub": "google-native-b",
                "user_id": second,
            },
        ]
        resolved, modes = module.resolve_requested_subjects(
            [first, "google-native-b"], rows
        )
        self.assertEqual(
            resolved,
            [
                {"provider": "google", "subject": first},
                {"provider": "google", "subject": second},
            ],
        )
        self.assertEqual(modes, {"supabase_user_id": 1, "google_provider_subject": 1})
        requested_path = ROOT / ".test-google-subjects.json"
        try:
            requested_path.write_text(
                json.dumps([first.upper(), "google-native-b"]), encoding="utf-8"
            )
            self.assertEqual(module.load_requested_subjects(requested_path)[0], first)
        finally:
            requested_path.unlink(missing_ok=True)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            module.resolve_requested_subjects(["missing", "google-native-b"], rows)
        with self.assertRaisesRegex(ValueError, "two distinct"):
            module.resolve_requested_subjects([first, "google-native-a"], rows)

        response = MagicMock(status_code=201)
        response.json.return_value = rows
        client = MagicMock()
        client.post.return_value = response
        queried = module.query_google_identities(
            project_ref="a" * 20,
            token="protected-management-token",
            requested=[first, "google-native-b"],
            client=client,
        )
        self.assertEqual(queried, rows)
        call = client.post.call_args
        self.assertTrue(call.args[0].endswith("/database/query/read-only"))
        self.assertEqual(call.kwargs["json"]["parameters"], [first, "google-native-b"])
        self.assertIn("$1", call.kwargs["json"]["query"])
        self.assertNotIn(first, call.kwargs["json"]["query"])

        emails = module.load_protected_emails("Primary@Example.com", "partner@example.com")
        self.assertEqual(emails, ["primary@example.com", "partner@example.com"])
        email_rows = [
            {
                "provider": "google",
                "user_id": first,
                "identity_email": "primary@example.com",
                "email_verified": True,
            },
            {
                "provider": "google",
                "user_id": second,
                "identity_email": "partner@example.com",
                "email_verified": True,
            },
        ]
        resolved, modes = module.resolve_protected_emails(emails, email_rows)
        self.assertEqual(
            resolved,
            [
                {"provider": "google", "subject": first},
                {"provider": "google", "subject": second},
            ],
        )
        self.assertEqual(
            modes,
            {"supabase_user_id": 2, "verified_google_email_admission": 0},
        )
        response.json.return_value = email_rows
        module.query_google_identities(
            project_ref="a" * 20,
            token="protected-management-token",
            emails=emails,
            client=client,
        )
        email_call = client.post.call_args
        self.assertEqual(email_call.kwargs["json"]["parameters"], emails)
        self.assertNotIn("primary@example.com", email_call.kwargs["json"]["query"])
        self.assertNotIn("LIMIT", email_call.kwargs["json"]["query"].upper())
        admissions, admission_modes = module.resolve_protected_emails(
            emails, email_rows[:1]
        )
        self.assertEqual(
            admissions,
            [
                {"provider": "google", "subject": first},
                {"provider": "google_email", "subject": "partner@example.com"},
            ],
        )
        self.assertEqual(
            admission_modes,
            {"supabase_user_id": 1, "verified_google_email_admission": 1},
        )
        mismatched, _ = module.resolve_protected_emails(
            emails,
            [
                {**email_rows[0], "identity_email": "different@example.com"},
                email_rows[1],
            ],
        )
        self.assertEqual(mismatched[0]["provider"], "google_email")
        unverified, _ = module.resolve_protected_emails(
            emails,
            [{**email_rows[0], "email_verified": "true"}, email_rows[1]],
        )
        self.assertEqual(unverified[0]["provider"], "google_email")
        with self.assertRaisesRegex(ValueError, "multiple"):
            module.resolve_protected_emails(
                emails,
                [
                    *email_rows,
                    {**email_rows[0], "user_id": "33333333-3333-3333-3333-333333333333"},
                ],
            )

        production_release = (
            ROOT / ".github/workflows/production-release.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(
            production_release.index("resolve_google_acceptance_subjects.py"),
            production_release.index("configure_staged_auth_origin.py add"),
        )

        provision = _path_module(
            "provision_google_email_admission",
            ROOT / "scripts/release/provision_acceptance_identity.py",
        )
        admissions_path = ROOT / ".test-google-email-admissions.json"
        try:
            admissions_path.write_text(
                json.dumps(
                    [
                        {"provider": "google_email", "subject": "Primary@Example.com"},
                        {"provider": "google_email", "subject": "partner@example.com"},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                provision._subjects(admissions_path),
                [
                    ("google_email", "primary@example.com"),
                    ("google_email", "partner@example.com"),
                ],
            )
        finally:
            admissions_path.unlink(missing_ok=True)

    def test_auth_exchange_logs_stage_without_identity_or_token(self) -> None:
        source = (ROOT / "backend/app/routers/auth/google.py").read_text(encoding="utf-8")
        self.assertIn("google_session_exchange stage=capability_denied", source)
        self.assertIn("google_session_exchange stage=authorization_unavailable", source)
        self.assertIn("google_session_exchange stage=completed", source)
        self.assertIn('"google_email"', source)
        tree = ast.parse(source)
        logger_calls = [
            ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ]
        self.assertTrue(logger_calls)
        self.assertTrue(all("identity_hash" not in call for call in logger_calls))
        self.assertTrue(all("payload.access_token" not in call for call in logger_calls))

    def test_google_acceptance_session_cleanup_requires_zero_unrevoked(self) -> None:
        module = _path_module(
            "cleanup_google_auth_sessions",
            ROOT / "scripts/release/cleanup_google_auth_sessions.py",
        )
        observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        report = module.build_cleanup_report(
            deployment_id="dpl_target",
            before_total=2,
            before_unrevoked=2,
            revoked_now=2,
            after_unrevoked=0,
            completed_at=observed,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["revoked_now"], 2)
        self.assertEqual(report["after_unrevoked"], 0)
        failed = module.build_cleanup_report(
            deployment_id="dpl_target",
            before_total=2,
            before_unrevoked=2,
            revoked_now=1,
            after_unrevoked=1,
            completed_at=observed,
        )
        self.assertFalse(failed["passed"])

    def test_interactive_google_acceptance_requires_two_bound_sessions_without_raw_ids(self) -> None:
        module = _path_module(
            "wait_for_google_auth_sessions",
            ROOT / "scripts/release/wait_for_google_auth_sessions.py",
        )
        observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        bindings = [
            {
                "binding_id": "11111111-1111-1111-1111-111111111111",
                "provider": "google_email",
                "consumed_user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "consumed_at": observed,
                "revoked_at": None,
                "binding_expires_at": observed + timedelta(minutes=10),
                "session_id": "33333333-3333-3333-3333-333333333333",
                "session_user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "session_revoked_at": None,
                "session_expires_at": observed + timedelta(minutes=10),
                "active_refresh_tokens": 1,
                "active_refresh_expires_at": observed + timedelta(minutes=10),
            },
            {
                "binding_id": "22222222-2222-2222-2222-222222222222",
                "provider": "google",
                "consumed_user_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "consumed_at": observed,
                "revoked_at": None,
                "binding_expires_at": observed + timedelta(minutes=10),
                "session_id": "44444444-4444-4444-4444-444444444444",
                "session_user_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "session_revoked_at": None,
                "session_expires_at": observed + timedelta(minutes=10),
                "active_refresh_tokens": 1,
                "active_refresh_expires_at": observed + timedelta(minutes=10),
            },
        ]
        report = module.build_acceptance_report(
            bindings,
            source_sha="a" * 40,
            deployment_id="dpl_target",
            activation_id="55555555-5555-5555-5555-555555555555",
            hmac_key="e" * 32,
            lease_expires_at=observed + timedelta(minutes=10),
            observed_at=observed,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["consumed_bindings"], 2)
        self.assertEqual(report["distinct_users"], 2)
        self.assertEqual(report["linked_sessions"], 2)
        serialized = json.dumps(report, sort_keys=True)
        for row in bindings:
            self.assertNotIn(row["binding_id"], serialized)
            self.assertNotIn(row["consumed_user_id"], serialized)
            self.assertNotIn(row["session_id"], serialized)

        pending = [{**bindings[0], "consumed_user_id": None, "consumed_at": None,
                    "session_id": None, "session_user_id": None}, bindings[1]]
        self.assertIsNone(
            module.build_acceptance_report(
                pending,
                source_sha="a" * 40,
                deployment_id="dpl_target",
                activation_id="55555555-5555-5555-5555-555555555555",
                hmac_key="e" * 32,
                lease_expires_at=observed + timedelta(minutes=10),
                observed_at=observed,
            )
        )

        invalid_cases = (
            ({"session_revoked_at": observed}, "valid linked browser session"),
            ({"session_expires_at": observed}, "expired"),
            ({"active_refresh_tokens": 0}, "valid linked browser session"),
            ({"active_refresh_tokens": 2}, "valid linked browser session"),
            ({"binding_expires_at": observed + timedelta(minutes=11)}, "authority lease"),
            ({"session_expires_at": observed + timedelta(minutes=11)}, "authority lease"),
            ({"active_refresh_expires_at": observed + timedelta(minutes=11)}, "authority lease"),
        )
        for override, message in invalid_cases:
            with self.subTest(override=override):
                invalid = [{**row} for row in bindings]
                invalid[0].update(override)
                with self.assertRaisesRegex(ValueError, message):
                    module.build_acceptance_report(
                        invalid,
                        source_sha="a" * 40,
                        deployment_id="dpl_target",
                        activation_id="55555555-5555-5555-5555-555555555555",
                        hmac_key="e" * 32,
                        lease_expires_at=observed + timedelta(minutes=10),
                        observed_at=observed,
                    )

    def test_completion_evidence_must_prove_cleanup_before_activation_is_cleaned(self) -> None:
        module = _path_module(
            "manage_google_completion_evidence",
            ROOT / "scripts/release/manage_google_auth_only_activation.py",
        )
        source_sha = "a" * 40
        deployment_id = "dpl_target"
        valid = {
            "schema": "vowpic.google-auth-only-watchdog-evidence.v1",
            "passed": True,
            "source_sha": source_sha,
            "deployment_id": deployment_id,
            "sessions_zero": True,
            "bindings_zero": True,
        }
        self.assertEqual(
            module._validate_completion_evidence(
                json.dumps(valid).encode(),
                source_sha=source_sha,
                deployment_id=deployment_id,
            ),
            valid,
        )
        for invalid in (
            {**valid, "passed": False},
            {**valid, "sessions_zero": False},
            {**valid, "bindings_zero": False},
            {**valid, "deployment_id": "dpl_other"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module._validate_completion_evidence(
                        json.dumps(invalid).encode(),
                        source_sha=source_sha,
                        deployment_id=deployment_id,
                    )

    def test_watchdog_never_completes_activation_when_session_cleanup_is_not_zero(self) -> None:
        module = _path_module(
            "manage_google_watchdog_failure",
            ROOT / "scripts/release/manage_google_auth_only_activation.py",
        )
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {
                "source_sha": "a" * 40,
                "api_deployment_id": "dpl_target",
                "approval": "approved",
            }
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        apply_module = SimpleNamespace(apply_phase=MagicMock())
        session_module = SimpleNamespace(
            cleanup_sessions=MagicMock(
                return_value={"passed": False, "after_unrevoked": 1}
            )
        )
        binding_module = SimpleNamespace(
            cleanup_bindings=MagicMock(
                return_value={
                    "passed": True,
                    "after": {"unused_unrevoked": 0, "active_unused": 0},
                }
            )
        )
        with (
            patch("psycopg2.connect", return_value=connection),
            patch.object(
                module,
                "_load_release_module",
                side_effect=(apply_module, session_module, binding_module),
            ),
            patch.object(module, "complete_activation") as complete,
        ):
            with self.assertRaisesRegex(RuntimeError, "sessions remain"):
                module.reap_expired_activations(
                    "postgresql://unused",
                    activation_plan={"phases": {}},
                )
        complete.assert_not_called()

    def test_interactive_google_acceptance_rejects_duplicate_users_and_other_capabilities(self) -> None:
        module = _path_module(
            "wait_for_google_auth_sessions_boundary",
            ROOT / "scripts/release/wait_for_google_auth_sessions.py",
        )
        observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        bindings = [
            {
                "binding_id": str(uuid4()),
                "provider": "google",
                "consumed_user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "consumed_at": observed,
                "revoked_at": None,
                "binding_expires_at": observed + timedelta(minutes=10),
                "session_id": str(uuid4()),
                "session_user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "session_revoked_at": None,
                "session_expires_at": observed + timedelta(minutes=10),
                "active_refresh_tokens": 1,
                "active_refresh_expires_at": observed + timedelta(minutes=10),
            }
            for _ in range(2)
        ]
        with self.assertRaisesRegex(ValueError, "distinct users"):
            module.build_acceptance_report(
                bindings,
                source_sha="a" * 40,
                deployment_id="dpl_target",
                activation_id="55555555-5555-5555-5555-555555555555",
                hmac_key="e" * 32,
                lease_expires_at=observed + timedelta(minutes=10),
                observed_at=observed,
            )
        flags = [
            {
                "capability": capability,
                "state": "ACCEPTANCE_COHORT" if capability == "google_auth" else "OFF",
                "deployment_id": "dpl_target" if capability == "google_auth" else None,
                "release_activation_id": (
                    "55555555-5555-5555-5555-555555555555"
                    if capability == "google_auth" else None
                ),
                "runtime_bundle_id": "rtb_" + "b" * 64 if capability == "google_auth" else None,
                "worker_image_digest": None,
                "target_manifest_sha256": None,
                "expires_at": observed + timedelta(minutes=10) if capability == "google_auth" else None,
            }
            for capability in module.EXPECTED_CAPABILITIES
        ]
        module.validate_capability_boundary(
            flags,
            deployment_id="dpl_target",
            activation_id="55555555-5555-5555-5555-555555555555",
            runtime_bundle_id="rtb_" + "b" * 64,
            observed_at=observed,
        )
        next(row for row in flags if row["capability"] == "generation")["state"] = "ON"
        with self.assertRaisesRegex(ValueError, "non-Google"):
            module.validate_capability_boundary(
                flags,
                deployment_id="dpl_target",
                activation_id="55555555-5555-5555-5555-555555555555",
                runtime_bundle_id="rtb_" + "b" * 64,
                observed_at=observed,
            )


if __name__ == "__main__":
    unittest.main()
