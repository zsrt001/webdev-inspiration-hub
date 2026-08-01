"""Fail-closed contract for the approved website-backend EvoLink architecture."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import Settings  # noqa: E402


class BackendEvolinkRuntimeContractTest(unittest.TestCase):
    def test_production_defaults_to_the_website_backend_executor(self) -> None:
        settings = Settings(
            _env_file=None,
            runtime_environment="production",
            debug=False,
            task_execution_mode="auto",
        )

        self.assertEqual(settings.generation_execution_mode, "backend")
        self.assertTrue(settings.using_backend_generation_execution)
        self.assertFalse(settings.using_background_queue)

    def test_explicit_backend_mode_is_available_outside_development(self) -> None:
        settings = Settings(
            _env_file=None,
            runtime_environment="production",
            debug=False,
            task_execution_mode="backend",
        )

        self.assertEqual(settings.generation_execution_mode, "backend")

    def test_hobby_compatible_progress_and_scheduled_recovery_stay_in_the_backend(
        self,
    ) -> None:
        contract = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertFalse(contract.get("crons"))
        ops_source = (BACKEND / "app" / "routers" / "ops.py").read_text(
            encoding="utf-8"
        )
        orders_source = (BACKEND / "app" / "routers" / "orders.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('@router.post("/generation/maintain")', ops_source)
        self.assertNotIn('@router.get("/generation/maintain")', ops_source)
        self.assertIn('@router.post("/{order_id}/progress"', orders_source)
        self.assertIn("advance_order_generation_once(", orders_source)
        recovery = (
            ROOT / ".github" / "workflows" / "generation-recovery.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('cron: "*/5 * * * *"', recovery)
        self.assertIn("secrets.CLEANUP_CRON_TOKEN", recovery)
        self.assertIn("/api/v1/ops/generation/maintain", recovery)
        self.assertIn(
            "EXPECTED_PRODUCTION_ORIGIN: https://www.vowpic.com",
            recovery,
        )
        self.assertIn(
            'if [ "$PRODUCTION_BASE_URL" != "$EXPECTED_PRODUCTION_ORIGIN" ]',
            recovery,
        )
        self.assertIn("environment: production-observation", recovery)
        self.assertIn('if [ "$RELEASE_ROLE" = "SAFE_BASELINE" ]', recovery)
        self.assertIn('test "$RELEASE_ROLE" = "COMMERCIAL_7A"', recovery)
        self.assertIn("sync-maintenance-secret", recovery)
        self.assertIn(
            "secrets.ONE_TIME_MAINTENANCE_SECRET_PUBLISH_TOKEN",
            recovery,
        )
        self.assertIn("--env production-observation", recovery)
        self.assertIn(
            "gh secret delete ONE_TIME_MAINTENANCE_SECRET_PUBLISH_TOKEN",
            recovery,
        )
        self.assertIn('test "$GITHUB_SHA" = "$REQUIRED_MAIN_SHA"', recovery)
        self.assertIn("--max-redirs 0", recovery)
        self.assertIn('"human_required"', recovery)
        self.assertNotIn("Evolink", recovery)
        self.assertNotIn("EVOLINK_API_KEY", recovery)

    def test_release_plan_uses_one_executable_main_sha_sequence(self) -> None:
        plan = (
            ROOT / "docs" / "operations" / "vowpic-finite-production-closure-plan.md"
        ).read_text(encoding="utf-8")
        r4 = plan.index("| R4 PR/CI 与受控合并 |")
        r5 = plan.index("| R5 Preview Identity/Commercial |")
        r6 = plan.index("| R6 Production 凭据前置与工作流内数据门 |")
        r7 = plan.index("| R7 同一 Production 工作流的验收、正式域名与观察 |")
        self.assertLess(r4, r5)
        self.assertLess(r5, r6)
        self.assertLess(r6, r7)
        local_review = plan.index(
            "R1 本地测试、完整 diff 复核和 R2 独立复审必须早于 push、PR 和 merge"
        )
        merge_step = plan.index("→ merge main")
        google_step = plan.index(
            "→ 最后接入或由用户本人验收两套 Google 测试身份"
        )
        preview_step = plan.index(
            "→ 对同一 merge SHA 运行并验证 Preview Identity/Commercial"
        )
        production_prerequisite_step = plan.index(
            "→ 只读核验并补齐 Production workflow 所需的最小权限凭据、库存和恢复演练输入"
        )
        production_step = plan.index(
            "→ 以同一 merge SHA 与该 Preview run/attempt 触发唯一 Production workflow"
        )
        data_gate_step = plan.index("→ 在该 workflow 内依次完成 R6 数据门")
        staged_acceptance_step = plan.index(
            "→ 继续同一 workflow 的 R7 staged acceptance 和 Promote"
        )
        self.assertLess(local_review, merge_step)
        self.assertLess(merge_step, google_step)
        self.assertLess(google_step, preview_step)
        self.assertLess(preview_step, production_prerequisite_step)
        self.assertLess(production_prerequisite_step, production_step)
        self.assertLess(production_step, data_gate_step)
        self.assertLess(data_gate_step, staged_acceptance_step)
        self.assertNotIn(
            "测试、完整 diff 复核和独立复审必须早于 push、PR、merge 和 Production",
            plan,
        )
        self.assertIn("head SHA=merge SHA", plan)
        self.assertIn("同一 merge SHA 与 R5 的 Preview run/attempt", plan)

        integration = (
            ROOT / ".github" / "workflows" / "integration.yml"
        ).read_text(encoding="utf-8")
        production = (
            ROOT / ".github" / "workflows" / "production-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("--ref refs/heads/main", integration)
        self.assertIn("--ref refs/heads/main", production)
        self.assertIn("inputs.required_source_sha", production)

    def test_terminal_provider_callback_triggers_backend_reconciliation(self) -> None:
        callback_source = (
            BACKEND / "app" / "routers" / "provider_callbacks.py"
        ).read_text(encoding="utf-8")
        self.assertIn("await reconcile_generation_job(result.job_id)", callback_source)

    def test_unknown_manual_settlement_runbook_is_executable_and_fail_closed(
        self,
    ) -> None:
        runbook = (
            ROOT / "docs" / "OPERATIONS_RUNBOOK.md"
        ).read_text(encoding="utf-8")
        for required in (
            "GET /api/v1/admin/generation/manual-settlements",
            "POST /api/v1/admin/generation/manual-settlements/{job_id}/resolve",
            "BIND_PROVIDER_TASK",
            "CONFIRMED_NOT_ACCEPTED_RETRY",
            "FAIL_AND_SETTLE",
            "evidence SHA-256",
            "resolve_generation_ambiguous_submission",
            "credit ledger/reservation lineage",
            "must never trigger automatic replay",
        ):
            self.assertIn(required, runbook)
        self.assertIn(
            "absence of a task ID is not proof of non-acceptance",
            runbook,
        )
        self.assertIn("raw SQL or direct database patching is not", runbook)


class NoExternalGenerationWorkerContractTest(unittest.TestCase):
    FORBIDDEN_RELEASE_TOKENS = (
        "RAILWAY_",
        "setup-railway-cli",
        "PRODUCTION_REDIS_URL",
        "worker-host-contract",
        "run_approved_worker_host",
        "WORKER_STAGED",
        "WORKER_RUNNING",
        "worker_heartbeat_report",
    )

    RETIRED_PATHS = (
        ".github/actions/setup-railway-cli/action.yml",
        "backend/Dockerfile.worker",
        "backend/run_worker.py",
        "backend/scripts/worker_entrypoint.py",
        "release/worker-host-contract.json",
        "scripts/release/run_approved_worker_host.py",
        "scripts/release/verify_runtime_drain.py",
        "release/runtime-contracts.json",
        "docs/operations/worker-host-addendum.md",
    )

    ACTIVE_ARCHITECTURE_DOCS = (
        "docs/ARCHITECTURE.md",
        "docs/OPERATIONS_RUNBOOK.md",
        "docs/PRODUCTION_ACCEPTANCE.md",
        "docs/SUPABASE_SETUP.md",
        "docs/README.md",
        "docs/VERCEL_DEPLOYMENT.md",
        "docs/operations/production-quality-review.md",
        "docs/operations/risk-lockdown-runbook.md",
    )

    FORBIDDEN_ACTIVE_DOC_TOKENS = (
        "ephemeral Worker",
        "Worker-host target",
        "shared Preview Redis",
        "long-running Worker",
        "Worker heartbeat",
        "Worker digest",
        "restores Worker dispatch",
        "disables the Worker",
        "identity API, commercial API, Worker",
        "TASK_EXECUTION_MODE=arq",
    )

    def test_production_release_has_no_external_worker_or_redis_gate(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "production-release.yml"
        ).read_text(encoding="utf-8")

        for token in self.FORBIDDEN_RELEASE_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, workflow)

    def test_external_worker_deployment_files_are_retired(self) -> None:
        for relative in self.RETIRED_PATHS:
            with self.subTest(path=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_arq_is_not_a_runtime_dependency(self) -> None:
        requirement_files = (
            ROOT / "requirements.in",
            ROOT / "requirements.txt",
            BACKEND / "requirements.txt",
            BACKEND / "requirements.lock.txt",
            BACKEND / "requirements.windows.lock.txt",
        )

        for path in requirement_files:
            text = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                self.assertNotIn("arq", text)

    def test_active_architecture_docs_cannot_reinstate_external_worker_topology(
        self,
    ) -> None:
        for relative in self.ACTIVE_ARCHITECTURE_DOCS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in self.FORBIDDEN_ACTIVE_DOC_TOKENS:
                with self.subTest(path=relative, token=token):
                    self.assertNotIn(token, text)

        historical_design = (
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-07-10-vowpic-commercial-closure-design.md"
        ).read_text(encoding="utf-8")
        self.assertIn("历史决策记录，不是当前执行权威", historical_design)
        self.assertIn("当前 Production 不构建或部署独立 Worker 镜像", historical_design)


if __name__ == "__main__":
    unittest.main()
