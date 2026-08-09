"""Release evidence safety and workflow false-green regression tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ReleaseEvidenceSafetyTest(unittest.TestCase):
    def test_evidence_path_is_content_scoped_and_never_mutable_latest(self) -> None:
        from scripts.release.aggregate_gates import validate_evidence_path

        valid = (
            "artifacts/release/" + "a" * 40
            + "/12345-2/dpl_abc/02-integration/auth-session.json"
        )
        validate_evidence_path(valid)
        for invalid in (
            "artifacts/release/latest/auth.json",
            "artifacts/release/" + "a" * 40 + "/123-1/../auth.json",
            "tmp/auth.json",
            "artifacts/release/" + "a" * 40 + "/123-1/dpl_abc/auth.json",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_evidence_path(invalid)

    def test_evidence_payload_rejects_secrets_and_sensitive_values(self) -> None:
        from scripts.release.aggregate_gates import validate_secret_free

        validate_secret_free({"case_id": "auth", "request_id": "request-1"})
        for payload in (
            {"access_token": "secret"},
            {"nested": {"csrf_secret": "secret"}},
            {"message": "Bearer abc.def.ghi"},
            {"email": "person@example.com"},
            {"object_url": "https://storage.invalid/private/object"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_secret_free(payload)

    def test_cli_returns_nonzero_for_any_false_aggregate(self) -> None:
        from scripts.release.aggregate_gates import main

        contract = {
            "schema": "vowpic.gates.v1",
            "statuses": ["PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"],
            "profiles": {"pr": ["unit"]},
            "cases": [{
                "id": "unit",
                "layer": "pr",
                "profiles": ["pr"],
                "mandatory": True,
                "timeout_seconds": 60,
                "freshness_seconds": 3600,
                "report_schema": "vowpic.gate-evidence.v1",
                "na_eligible": False,
                "capability": None,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "gates.json"
            output_path = root / "aggregate.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            exit_code = main([
                "--contract", str(contract_path),
                "--profile", "pr",
                "--expected-source-sha", "a" * 40,
                "--expected-runtime-bundle-id", "rtb_" + "b" * 64,
                "--output", str(output_path),
            ])
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "FAIL")


class WorkflowFalseGreenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.integration = (ROOT / ".github/workflows/integration.yml").read_text(encoding="utf-8")
        self.playwright_config = (
            ROOT / "frontend/playwright.config.ts"
        ).read_text(encoding="utf-8")
        self.a11y_test_path = ROOT / "frontend/e2e/accessibility.spec.ts"
        self.preview_test = (
            ROOT / "frontend/e2e/google-session-smoke.spec.ts"
        ).read_text(encoding="utf-8")
        self.main_flow_test = (
            ROOT / "frontend/e2e/main-flow.spec.ts"
        ).read_text(encoding="utf-8")
        self.linked_acceptance = (
            ROOT / "frontend/e2e/helpers/linked-acceptance.ts"
        ).read_text(encoding="utf-8")
        self.google_oauth_acceptance = (
            ROOT / "frontend/e2e/helpers/google-oauth-acceptance.ts"
        ).read_text(encoding="utf-8")
        self.browser_auth = (
            ROOT / "frontend/src/services/auth.ts"
        ).read_text(encoding="utf-8")
        self.cleanup = (
            ROOT / "scripts/release/cleanup_preview_identity_smoke.py"
        ).read_text(encoding="utf-8")

    def test_google_handoff_readiness_never_automates_or_contacts_google_login(self) -> None:
        readiness = (
            ROOT / "frontend/e2e/google-oauth-handoff-readiness.spec.ts"
        ).read_text(encoding="utf-8")

        def assert_context_wide_google_boundary(source: str) -> None:
            self.assertIn("test.use({ serviceWorkers: 'block' })", source)
            self.assertIn("context.waitForEvent('request'", source)
            self.assertIn("context.route('https://accounts.google.com/**'", source)
            self.assertIn("outgoing.resourceType() === 'document'", source)
            self.assertIn("url.pathname === '/o/oauth2/v2/auth'", source)
            self.assertIn("oauthDocumentAborted = true", source)
            self.assertIn("expect(googleResponses).toEqual([])", source)
            self.assertNotIn("const googleRequest = page.waitForRequest", source)
            self.assertNotIn("page.route('https://accounts.google.com/**'", source)

        assert_context_wide_google_boundary(readiness)
        for label, mutated in {
            "popup_escape": readiness.replace(
                "context.route('https://accounts.google.com/**'",
                "page.route('https://accounts.google.com/**'",
                1,
            ),
            "page_scoped_listener": readiness.replace(
                "const googleRequest = context.waitForEvent('request'",
                "const googleRequest = page.waitForRequest",
                1,
            ),
            "service_worker_escape": readiness.replace(
                "test.use({ serviceWorkers: 'block' })", "", 1
            ),
        }.items():
            with self.subTest(mutation=label), self.assertRaises(AssertionError):
                assert_context_wide_google_boundary(mutated)

        self.assertIn("RUN_GOOGLE_HANDOFF_E2E", self.playwright_config)
        self.assertIn("googleHandoffRun", self.playwright_config)
        self.assertIn("route.abort('blockedbyclient')", readiness)
        self.assertNotIn("interceptedGoogleURL", readiness)
        self.assertIn("/auth/v1/authorize", readiness)
        self.assertIn("code_challenge", readiness)
        self.assertIn("code_challenge_method", readiness)
        self.assertIn("deferred_to_production_google_only", readiness)
        for forbidden in (
            "storageState",
            "PREVIEW_GOOGLE_EMAIL",
            "PREVIEW_GOOGLE_STORAGE_STATE",
            ".fill(",
            "password",
            "page.goto('https://accounts.google.com",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, readiness)
        self.assertIn(
            "vowpic-preview-google-readiness-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}",
            self.integration,
        )
        readiness_job = self.integration[
            self.integration.index("  google-readiness:\n") :
            self.integration.index("  cleanup:\n")
        ]
        self.assertNotIn("PREVIEW_GOOGLE_STORAGE_STATE", readiness_job)
        self.assertNotIn("PREVIEW_GOOGLE_EMAIL", readiness_job)

    def test_pr_ci_has_nonempty_tests_and_no_external_secret_or_deploy_path(self) -> None:
        self.assertIn("collected_backend_tests", self.ci)
        self.assertIn("npm run test:unit", self.ci)
        self.assertIn("alembic upgrade head", self.ci)
        self.assertIn("backend.tests.integration.test_identity_rls", self.ci)
        self.assertIn("backend.tests.integration.test_partner_invite_rls", self.ci)
        self.assertIn("backend-execution-contract:", self.ci)
        self.assertIn("website-backend EvoLink execution boundary", self.ci)
        self.assertNotIn("docker build --file backend/Dockerfile.worker", self.ci)
        self.assertIn("scripts/release/aggregate_gates.py", self.ci)
        self.assertIn("--profile pr", self.ci)
        self.assertIn("artifacts/release/", self.ci)
        self.assertNotIn("--passWithNoTests", self.ci)
        self.assertNotIn("secrets.", self.ci)
        self.assertNotIn("vercel deploy", self.ci)
        self.assertNotIn("domains add", self.ci)
        self.assertNotIn("continue-on-error", self.ci)

    def test_pr_accessibility_is_real_local_browser_work_not_a_skip(self) -> None:
        self.assertTrue(self.a11y_test_path.exists())
        a11y = self.a11y_test_path.read_text(encoding="utf-8")
        self.assertIn("AxeBuilder", a11y)
        self.assertIn("@a11y", a11y)
        self.assertIn("serious", a11y)
        self.assertIn("critical", a11y)
        self.assertNotIn("route.fulfill", a11y)
        self.assertIn("RUN_LOCAL_A11Y", self.playwright_config)
        self.assertIn("webServer", self.playwright_config)
        self.assertIn("npm run test:a11y", self.ci)
        self.assertIn("npm run playwright:install", self.ci)
        self.assertNotIn("--pass-with-no-tests", self.ci.lower())

    def test_preview_is_manual_protected_pinned_and_missing_secrets_fail(self) -> None:
        self.assertIn("workflow_dispatch:", self.integration)
        self.assertNotIn("pull_request:", self.integration)
        self.assertNotIn("push:\n", self.integration)
        self.assertIn("environment: preview-identity", self.integration)
        self.assertIn("npm run playwright:install --prefix frontend", self.integration)
        self.assertIn("test -n \"$PREVIEW_MIGRATION_DATABASE_URL\"", self.integration)
        self.assertIn("test -n \"$PREVIEW_PRIVATE_BLOB_READ_WRITE_TOKEN\"", self.integration)
        self.assertIn("if: always()", self.integration)
        self.assertNotIn("continue-on-error", self.integration)
        self.assertNotIn("Missing secret; skipping", self.integration)
        for line in self.integration.splitlines():
            if "uses:" in line:
                with self.subTest(line=line):
                    self.assertRegex(line, r"uses:\s+[^\s]+@[0-9a-f]{40}")

    def test_preview_e2e_extends_real_login_with_private_media_isolation(self) -> None:
        for required in (
            "/api/v1/media/uploads",
            "/api/v1/media/",
            "PREVIEW_SECOND_GOOGLE_STORAGE_STATE_PATH",
            "cross-user",
            "DELETE",
            "PREVIEW_FAILURE_INJECTION:${boundary}",
            "injectFailure('upload')",
            "injectFailure('owner-read')",
            "injectFailure('cross-user')",
            "injectFailure('delete')",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.preview_test)

    def test_protected_google_acceptance_uses_headed_edge_and_exact_hint_without_changing_user_login(self) -> None:
        self.assertIn("url.searchParams.delete('prompt')", self.google_oauth_acceptance)
        self.assertIn("url.searchParams.set('login_hint', identityEmail)", self.google_oauth_acceptance)
        self.assertIn("{ times: 1 }", self.google_oauth_acceptance)
        self.assertIn("queryParams: { prompt: 'select_account' }", self.browser_auth)
        self.assertIn("npm run test:e2e -- --headed e2e/google-session-smoke.spec.ts", self.integration)
        for source in (
            self.preview_test,
            self.main_flow_test,
            self.linked_acceptance,
        ):
            with self.subTest(source=source[:80]):
                self.assertIn("preparePreauthenticatedGoogleOAuth", source)
        self.assertNotIn("route.fulfill", self.preview_test)
        self.assertNotIn("mock", self.preview_test.lower())

    def test_preview_google_login_uses_exact_account_identity_and_safe_failure_evidence(self) -> None:
        self.assertIn('[data-identifier="${escapedEmail}"]', self.preview_test)
        self.assertIn("GOOGLE_ACCOUNT_SELECTOR_AMBIGUOUS", self.preview_test)
        self.assertIn("GOOGLE_AUTH_RETURN_TIMEOUT", self.preview_test)
        self.assertIn("password_field", self.preview_test)
        self.assertIn("verification_code_field", self.preview_test)
        self.assertIn("继续|允许|同意|确认", self.preview_test)
        self.assertNotIn("page.getByText(email, { exact: false }).first()", self.preview_test)

    def test_protected_main_flow_uses_exact_google_account_identity_and_safe_failure_evidence(self) -> None:
        self.assertIn('[data-identifier="${escapedEmail}"]', self.main_flow_test)
        self.assertIn("Google account selector must be unambiguous", self.main_flow_test)
        self.assertIn("GOOGLE_AUTH_RETURN_TIMEOUT", self.main_flow_test)
        self.assertIn("password_field", self.main_flow_test)
        self.assertIn("verification_code_field", self.main_flow_test)
        self.assertNotIn(
            "page.getByText(identityEmail, { exact: false }).first()",
            self.main_flow_test,
        )

    def test_preview_emits_only_real_pass_cases_for_the_full_release_scope(self) -> None:
        for case_id in (
            "preview_google_session",
            "preview_private_media_owner_read",
            "preview_private_media_cross_user",
            "preview_private_media_delete",
            "preview_creem_test_contract",
            "preview_main_journey",
            "preview_account_export",
            "preview_account_delete",
            "preview_partner_invite",
            "preview_accessibility_visual",
            "preview_cleanup",
        ):
            with self.subTest(case_id=case_id):
                self.assertIn(case_id, self.integration)
        self.assertNotIn('"PASS" if passed else "NOT_RUN"', self.integration)
        self.assertNotIn("explicit Stage-6 NOT_RUN evidence", self.integration)
        self.assertIn("materialize_preview_release_evidence.py", self.integration)
        self.assertIn("verify_preview_release_package.py build", self.integration)
        self.assertIn('"status": "PASS"', self.integration)
        self.assertIn('"schema": "vowpic.gate-evidence.v1"', self.integration)
        self.assertIn("artifacts/release/${SOURCE_SHA}", self.integration)

    def test_cleanup_is_independent_and_verifies_asset_prefix_and_two_bindings(self) -> None:
        for required in (
            "private_asset_prefix",
            "second_binding",
            "storage_objects_remaining",
            "origin_restored",
            "feature flags are not fully OFF",
            '"state": "CLEANED"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.cleanup)


if __name__ == "__main__":
    unittest.main()
