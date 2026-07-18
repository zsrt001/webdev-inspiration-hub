"""Task 4 CI, dependency-lock, and one-time safe-baseline contracts."""

from __future__ import annotations

import importlib.util
import inspect
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import httpx


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.exists():
        raise AssertionError(f"required Task 4 artifact is missing: {relative_path}")
    return path.read_text(encoding="utf-8")


def _load_script(relative_path: str, module_name: str):
    path = ROOT / relative_path
    if not path.exists():
        raise AssertionError(f"required Task 4 script is missing: {relative_path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hashed_requirement_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace() and not stripped.startswith("--hash="):
            if current:
                blocks.append("\n".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))
    return blocks


class CiReleaseContractTest(unittest.TestCase):
    def test_first_party_actions_pin_verified_node24_release_commits(self) -> None:
        expected_actions = {
            "actions/checkout": (
                "df4cb1c069e1874edd31b4311f1884172cec0e10",
                "v6.0.3",
            ),
            "actions/setup-python": (
                "ece7cb06caefa5fff74198d8649806c4678c61a1",
                "v6.3.0",
            ),
            "actions/setup-node": (
                "249970729cb0ef3589644e2896645e5dc5ba9c38",
                "v6.5.0",
            ),
            "actions/upload-artifact": (
                "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
                "v7.0.1",
            ),
            "actions/download-artifact": (
                "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
                "v8.0.1",
            ),
        }
        action_pattern = re.compile(
            r"uses:\s*(actions/[a-z-]+)@([0-9a-f]{40})\s*#\s*(v[0-9.]+)"
        )
        seen_actions: set[str] = set()

        for workflow_path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            workflow = workflow_path.read_text(encoding="utf-8")
            action_lines = [
                line for line in workflow.splitlines() if "uses: actions/" in line
            ]
            matches = action_pattern.findall(workflow)
            self.assertEqual(len(matches), len(action_lines), workflow_path.name)
            for action_name, commit_sha, version in matches:
                with self.subTest(workflow=workflow_path.name, action=action_name):
                    self.assertIn(action_name, expected_actions)
                    self.assertEqual(
                        (commit_sha, version),
                        expected_actions[action_name],
                    )
                    seen_actions.add(action_name)

        self.assertEqual(seen_actions, set(expected_actions))

    def test_safe_baseline_pins_vercel_compatible_uv_before_build(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        setup = workflow.index(
            "uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2"
        )
        release_tools = workflow.index("- name: Install the hash-locked release tooling")
        build = workflow.index('"$VERCEL_CLI" build')
        setup_step = workflow[
            workflow.rindex("- name:", 0, setup):release_tools
        ]
        tooling_step = workflow[release_tools:build]

        self.assertIn('version: "0.10.11"', setup_step)
        self.assertIn(
            'checksum: "5a360b0de092ddf4131f5313d0411b48c4e95e8107e40c3f8f2e9fcb636b3583"',
            setup_step,
        )
        self.assertIn("enable-cache: false", setup_step)
        self.assertIn("download-from-astral-mirror: false", setup_step)
        self.assertIn('test "$(uv --version)" = "uv 0.10.11"', tooling_step)
        self.assertLess(setup, release_tools)
        self.assertLess(release_tools, build)
        self.assertEqual(workflow.count("uses: astral-sh/setup-uv@"), 1)

    def test_upload_artifact_raw_digest_is_canonicalized_before_binding(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        step = workflow[
            workflow.index("- name: Resolve and bind one durable build manifest"):
            workflow.index("- name: Recheck current main before staged deployment")
        ]

        self.assertIn(
            '[[ "$RECOVERED_BUILD_ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]',
            step,
        )
        self.assertIn(
            '[[ "$NEW_BUILD_ARTIFACT_DIGEST" =~ ^[0-9a-f]{64}$ ]]',
            step,
        )
        self.assertIn(
            'BUILD_ARTIFACT_DIGEST="sha256:$NEW_BUILD_ARTIFACT_DIGEST"',
            step,
        )
        self.assertIn(
            '[[ "$BUILD_ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]',
            step,
        )
        self.assertNotIn(
            '${RECOVERED_BUILD_ARTIFACT_DIGEST:-$NEW_BUILD_ARTIFACT_DIGEST}',
            step,
        )

    def test_backend_ci_runs_the_real_control_plane_rls_postgresql_test(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        backend_job = workflow[
            workflow.index("  backend-test:") : workflow.index("  frontend-check:")
        ]
        self.assertIn("services:", backend_job)
        self.assertRegex(backend_job, r"image:\s*postgres:15-alpine@sha256:[0-9a-f]{64}")
        self.assertIn("RUN_POSTGRES_INTEGRATION: '1'", backend_job)
        self.assertIn("CONTROL_PLANE_RLS_TEST_DATABASE_URL:", backend_job)
        self.assertIn("backend.tests.integration.test_control_plane_rls", backend_job)
        self.assertIn("RUN_CLICK_STATS_REPAIR_INTEGRATION: '1'", backend_job)
        self.assertIn("CLICK_STATS_REPAIR_TEST_DATABASE_URL:", backend_job)
        self.assertIn("backend.tests.integration.test_click_stats_repair", backend_job)

    def test_backend_ci_waits_for_the_final_postgres_server_before_integration_tests(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        backend_job = workflow[
            workflow.index("  backend-test:") : workflow.index("  frontend-check:")
        ]
        wait_step = "- name: Wait for PostgreSQL final startup"
        first_integration_step = "- name: Run the real PostgreSQL control-plane RLS contract"

        self.assertIn(wait_step, backend_job)
        self.assertLess(backend_job.index(wait_step), backend_job.index(first_integration_step))
        self.assertIn("POSTGRES_SERVICE_ID: ${{ job.services.postgres.id }}", backend_job)
        self.assertIn("deadline=$((SECONDS + 120))", backend_job)
        self.assertIn(
            "PostgreSQL init process complete; ready for start up.",
            backend_job,
        )
        self.assertIn(
            'docker exec "$POSTGRES_SERVICE_ID" pg_isready '
            "-U postgres -d vowpic_rls_test",
            backend_job,
        )
        self.assertIn('docker logs --tail 100 "$POSTGRES_SERVICE_ID"', backend_job)

    def test_ci_resolver_uses_current_pinned_python_311_patch_image(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        self.assertIn(
            "python:3.11.15-slim-bookworm@sha256:"
            "721dc13fd1be0a771e54b72097634291d628d0007dee9da777e2ce676a9c998f",
            workflow,
        )

    def test_ci_proves_the_pinned_resolver_runtime_identity(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        linux_job = workflow[
            workflow.index("  dependency-locks:") : workflow.index(
                "  vercel-dependency-locks:"
            )
        ]
        self.assertIn("platform.python_version()", linux_job)
        self.assertIn("3.11.15", linux_job)
        self.assertIn("VERSION_CODENAME", linux_job)
        self.assertIn("bookworm", linux_job)

    def test_ci_runs_pip_check_for_each_linux_python_graph(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        dependency_job = workflow[
            workflow.index("  dependency-locks:") : workflow.index(
                "  vercel-dependency-locks:"
            )
        ]
        vercel_dependency_job = workflow[
            workflow.index("  vercel-dependency-locks:") : workflow.index(
                "  windows-dependency-locks:"
            )
        ]
        backend_job = workflow[
            workflow.index("  backend-test:") : workflow.index("  frontend-check:")
        ]
        self.assertIn("python -m pip check", dependency_job)
        self.assertIn("python -m pip check", vercel_dependency_job)
        self.assertIn("python -m pip check", backend_job)

    def test_vercel_runtime_and_lock_job_are_python_312_aligned(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        vercel_job = workflow[
            workflow.index("  vercel-dependency-locks:") : workflow.index(
                "  windows-dependency-locks:"
            )
        ]
        self.assertEqual(_read(".python-version").strip(), "3.12")
        self.assertRegex(
            vercel_job,
            r"container:\s*python:3\.12\.13-slim-bookworm@sha256:[0-9a-f]{64}",
        )
        self.assertIn("platform.python_version()", vercel_job)
        self.assertIn("3.12.13", vercel_job)
        self.assertIn("VERSION_CODENAME", vercel_job)
        self.assertIn("bookworm", vercel_job)
        self.assertIn("requirements.in", vercel_job)
        self.assertIn("requirements.txt", vercel_job)
        self.assertIn("--require-hashes -r requirements.txt", vercel_job)
        self.assertIn("import api.index", vercel_job)
        root_lock = _read("requirements.txt")
        self.assertIn("pip-compile with Python 3.12", root_lock)
        self.assertIn("vercel-workers==0.0.25", root_lock)

    def test_ci_and_release_use_a_supported_exact_node_lts(self) -> None:
        for relative_path in (
            ".github/workflows/ci.yml",
            ".github/workflows/safe-baseline-release.yml",
        ):
            workflow = _read(relative_path)
            self.assertIn('node-version: "24.17.0"', workflow)
            self.assertNotIn('node-version: "20"', workflow)

    def test_resolver_bootstrap_is_hash_locked(self) -> None:
        resolver_input = _read("requirements-resolver.in")
        resolver_lock = _read("requirements-resolver.txt")
        self.assertEqual(
            resolver_input.splitlines(),
            ["build==1.5.0", "pip-tools==7.5.3"],
        )
        blocks = _hashed_requirement_blocks(resolver_lock)
        self.assertGreater(len(blocks), 0)
        self.assertTrue(any(block.startswith("build==1.5.0") for block in blocks))
        self.assertTrue(any(block.startswith("pip-tools==7.5.3") for block in blocks))
        for block in blocks:
            self.assertIn("--hash=sha256:", block)
        workflow = _read(".github/workflows/ci.yml")
        self.assertIn("--require-hashes -r requirements-resolver.txt", workflow)
        self.assertNotIn(
            "pip install --disable-pip-version-check pip-tools==7.5.3",
            workflow,
        )

    def test_windows_resolver_and_backend_locks_are_independently_reproduced(self) -> None:
        resolver_lock = _read("requirements-resolver.windows.txt")
        backend_lock = _read("backend/requirements.windows.lock.txt")
        for content in (resolver_lock, backend_lock):
            blocks = _hashed_requirement_blocks(content)
            self.assertGreater(len(blocks), 0)
            for block in blocks:
                self.assertIn("--hash=sha256:", block)
        self.assertIn("pip-tools==7.5.3", resolver_lock)
        self.assertIn("colorama==0.4.6", resolver_lock)
        self.assertIn("colorama==0.4.6", backend_lock)
        workflow = _read(".github/workflows/ci.yml")
        self.assertIn("windows-dependency-locks:", workflow)
        self.assertIn("runs-on: windows-2022", workflow)
        self.assertIn(
            "--require-hashes -r requirements-resolver.windows.txt",
            workflow,
        )
        self.assertIn("backend/requirements.windows.lock.txt", workflow)

    def test_windows_lock_job_uses_an_available_exact_python_311_patch(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        windows_job = workflow[
            workflow.index("  windows-dependency-locks:") : workflow.index(
                "  backend-test:"
            )
        ]
        self.assertIn('python-version: "3.11.9"', windows_job)
        self.assertNotIn('python-version: "3.11.15"', windows_job)
        self.assertIn("platform.python_version()", windows_job)
        self.assertIn("3.11.9", windows_job)

    def test_pr_workflow_is_secret_free_and_contains_no_production_deploy(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        self.assertNotIn("vercel deploy --prebuilt --prod", workflow)
        self.assertNotIn("relying on Vercel Git integration", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s{2}(deploy|production-smoke):\s*$")
        self.assertNotIn("${{ secrets.", workflow)
        self.assertIn("quality-gate", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("countTestCases()", workflow)
        self.assertIn("--require-hashes -r backend/requirements.lock.txt", workflow)
        self.assertIn("npm run typecheck", workflow)
        self.assertIn("npm run build:web", workflow)
        self.assertIn("vowpic-pr-gates-${{ github.run_id }}-${{ github.run_attempt }}", workflow)
        self.assertIn("artifacts/release/${{ github.sha }}/${{ github.run_id }}-${{ github.run_attempt }}/pr/01-ci/*.json", workflow)
        self.assertIn("npm run test:unit", workflow)
        for result_env in (
            "LINUX_LOCKS_RESULT",
            "VERCEL_LOCKS_RESULT",
            "WINDOWS_LOCKS_RESULT",
            "BACKEND_RESULT",
            "FRONTEND_RESULT",
            "WORKER_IMAGE_RESULT",
        ):
            self.assertIn(f'os.environ["{result_env}"] == "success"', workflow)

    def test_vercel_git_main_auto_deployment_is_disabled_but_preview_remains_available(self) -> None:
        config = json.loads(_read("vercel.json"))
        deployment_enabled = config["git"]["deploymentEnabled"]
        self.assertIs(deployment_enabled["main"], False)
        self.assertEqual(set(deployment_enabled), {"main"})

    def test_vercel_routes_the_legacy_user_slash_form_to_the_api_function(self) -> None:
        config = json.loads(_read("vercel.json"))
        rewrites = config["rewrites"]
        rewrite_by_source = {
            item["source"]: item["destination"]
            for item in rewrites
        }
        sources = [item["source"] for item in rewrites]

        self.assertEqual(
            rewrite_by_source["/api/v1/users/"],
            "/api/index.py",
        )
        self.assertLess(
            sources.index("/api/v1/users/"),
            sources.index("/api/:path*"),
        )
        self.assertLess(
            sources.index("/api/v1/users/"),
            sources.index("/(.*)"),
        )

    def test_vercel_runtime_environment_comes_from_platform_system_environment(self) -> None:
        config = json.loads(_read("vercel.json"))

        self.assertNotIn("RUNTIME_ENVIRONMENT", config.get("env", {}))

    def test_vercel_function_bundles_the_pinned_supabase_root_certificate(self) -> None:
        config = json.loads(_read("vercel.json"))
        certificate = ROOT / "backend" / "app" / "core" / "certs" / "prod-ca-2021.crt"

        self.assertEqual(
            config["functions"]["api/index.py"]["includeFiles"],
            "backend/app/core/certs/prod-ca-2021.crt",
        )
        self.assertTrue(certificate.is_file())
        self.assertEqual(
            hashlib.sha256(certificate.read_bytes()).hexdigest(),
            "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7",
        )

    def test_vercel_upload_context_keeps_required_frontend_build_script(self) -> None:
        ignore_lines = {
            line.strip()
            for line in _read(".vercelignore").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("/scripts", ignore_lines)
        self.assertNotIn("scripts", ignore_lines)
        clean_script = ROOT / "frontend" / "scripts" / "clean-web-output.mjs"
        self.assertTrue(clean_script.is_file())

    def test_frontend_core_tool_versions_and_scripts_are_exact(self) -> None:
        package = json.loads(_read("frontend/package.json"))
        expected = {
            "vue": "3.4.21",
            "typescript": "5.3.3",
            "vite": "5.2.8",
            "vitest": "3.2.6",
            "vue-tsc": "2.2.12",
            "sass": "1.97.3",
        }
        merged = {**package["dependencies"], **package["devDependencies"]}
        for name, version in expected.items():
            self.assertEqual(merged[name], version)
        self.assertEqual(package["scripts"]["typecheck"], "vue-tsc --noEmit")
        self.assertEqual(package["scripts"]["test:unit"], "vitest run")
        self.assertEqual(
            package["scripts"]["dev:web"],
            "uni -p h5 --host 127.0.0.1",
        )
        self.assertEqual(
            package["scripts"]["build:web"],
            "node scripts/clean-web-output.mjs && uni build -p h5",
        )

        lock = json.loads(_read("frontend/package-lock.json"))
        root_package = lock["packages"][""]
        merged_lock = {
            **root_package["dependencies"],
            **root_package["devDependencies"],
        }
        for name, version in expected.items():
            self.assertEqual(merged_lock[name], version)
            self.assertEqual(lock["packages"][f"node_modules/{name}"]["version"], version)

        expected_overrides = {
            "@babel/core": "7.29.6",
            "@babel/plugin-transform-modules-systemjs": "7.29.4",
            "@intlify/core-base": "9.14.5",
            "@intlify/message-resolver": "9.1.11",
            "adm-zip": "0.6.0",
            "cookie": "0.7.0",
            "esbuild": "0.25.0",
            "glob": "10.5.0",
            "immutable": "5.1.5",
            "path-to-regexp": "0.1.13",
            "picomatch@2.3.1": "2.3.2",
            "picomatch@4.0.3": "4.0.4",
            "postcss": "8.5.10",
            "qs": "6.15.2",
            "rollup": "4.59.0",
            "send": "0.19.0",
            "yaml": "1.10.3",
        }
        self.assertEqual(package["overrides"], expected_overrides)
        self.assertEqual(lock["packages"]["node_modules/adm-zip"]["version"], "0.6.0")

    def test_unused_uni_automator_and_its_jest27_chain_are_absent(self) -> None:
        package = json.loads(_read("frontend/package.json"))
        self.assertNotIn("@dcloudio/uni-automator", package["devDependencies"])
        lock = json.loads(_read("frontend/package-lock.json"))
        root_package = lock["packages"][""]
        self.assertNotIn(
            "@dcloudio/uni-automator",
            root_package.get("devDependencies", {}),
        )
        for path in (
            "node_modules/@dcloudio/uni-automator",
            "node_modules/jest",
            "node_modules/jest-cli",
            "node_modules/jest-environment-jsdom",
        ):
            with self.subTest(path=path):
                self.assertNotIn(path, lock["packages"])

    def test_frontend_locked_installs_do_not_execute_package_lifecycle_scripts(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        frontend_job = workflow[
            workflow.index("  frontend-check:") : workflow.index("  quality-gate:")
        ]
        baseline = _read("scripts/release/verify_baseline.ps1")
        vercel = json.loads(_read("vercel.json"))
        self.assertIn("npm ci --ignore-scripts", frontend_job)
        self.assertIn("npm ci --ignore-scripts", baseline)
        self.assertEqual(
            vercel["installCommand"],
            "cd frontend && npm ci --ignore-scripts",
        )
        self.assertNotRegex(frontend_job, r"(?m)^\s*- run: npm ci\s*$")

    def test_local_baseline_discovers_tests_from_the_repository_root(self) -> None:
        baseline = _read("scripts/release/verify_baseline.ps1")
        self.assertIn(
            'discover("backend/tests", top_level_dir=".")',
            baseline,
        )

    def test_frontend_scss_uses_modules_for_local_styles(self) -> None:
        deprecated_imports: list[str] = []
        source_root = ROOT / "frontend" / "src"
        for pattern in ("*.vue", "*.scss", "*.sass"):
            for path in source_root.rglob(pattern):
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(),
                    start=1,
                ):
                    stripped = line.strip()
                    if stripped.startswith("@import") and not stripped.startswith(
                        "@import url("
                    ):
                        deprecated_imports.append(
                            f"{path.relative_to(ROOT).as_posix()}:{line_number}"
                        )
        self.assertEqual(deprecated_imports, [])

    def test_python_locks_are_exact_hashed_and_framework_aligned(self) -> None:
        root_input = _read("requirements.in")
        backend_input = _read("backend/requirements.txt")
        for content in (root_input, backend_input):
            self.assertIn("fastapi==0.139.0", content)
            self.assertIn("starlette==1.3.1", content)

    def test_uvicorn_standard_dependencies_keep_the_linux_only_marker(self) -> None:
        for relative_path in ("requirements.in", "backend/requirements.txt"):
            content = _read(relative_path)
            self.assertNotIn("uvicorn[standard]", content)
            self.assertRegex(content, r"(?m)^uvicorn>=0\.27\.0$")
            self.assertRegex(
                content,
                r"(?m)^uvloop>=0\.15\.1\s*;.*sys_platform\s*!=\s*[\"']win32[\"']",
            )

    def test_backend_lock_pins_the_windows_only_click_dependency(self) -> None:
        marker = r"(?m)^colorama==0\.4\.6\s*;\s*sys_platform\s*==\s*[\"']win32[\"']"
        self.assertRegex(_read("backend/requirements.txt"), marker)
        colorama_block = next(
            block
            for block in _hashed_requirement_blocks(
                _read("backend/requirements.windows.lock.txt")
            )
            if block.startswith("colorama==0.4.6")
        )
        self.assertRegex(colorama_block.splitlines()[0], marker)
        self.assertIn("--hash=sha256:", colorama_block)

        for relative_path in ("requirements.txt", "backend/requirements.lock.txt"):
            content = _read(relative_path)
            blocks = _hashed_requirement_blocks(content)
            self.assertGreater(len(blocks), 10)
            for block in blocks:
                header = block.splitlines()[0]
                with self.subTest(path=relative_path, requirement=header):
                    self.assertRegex(
                        header,
                        r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==[^\s\\]+(?:\s*;.*)?(?:\s*\\)?$",
                    )
                    self.assertIn("--hash=sha256:", block)
                    self.assertNotIn(">=", block)
                    self.assertNotIn("~=", block)
            self.assertIn("fastapi==0.139.0", content)
            self.assertIn("starlette==1.3.1", content)

    def test_linux_resolver_contract_runs_twice_and_checks_committed_locks(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        linux_job = workflow[
            workflow.index("  dependency-locks:") : workflow.index(
                "  vercel-dependency-locks:"
            )
        ]
        self.assertIn("python:3.11", linux_job)
        self.assertRegex(
            linux_job,
            r"container:\s*python:3\.11\.15-slim-bookworm@sha256:[0-9a-f]{64}",
        )
        self.assertIn("--require-hashes -r requirements-resolver.txt", linux_job)
        self.assertGreaterEqual(linux_job.count("python -m piptools compile"), 3)
        self.assertNotIn("requirements.in", linux_job)
        self.assertIn("backend/requirements.txt", linux_job)
        self.assertIn("backend/requirements.lock.txt", linux_job)
        self.assertIn("sha256sum", linux_job)
        self.assertIn("git diffutils", linux_job)
        for expected_snapshot in (
            "cp requirements-resolver.txt /tmp/requirements-resolver.expected.txt",
            "cp backend/requirements.lock.txt /tmp/backend-requirements-lock.expected.txt",
            "cmp /tmp/requirements-resolver.expected.txt requirements-resolver.txt",
            "cmp /tmp/backend-requirements-lock.expected.txt backend/requirements.lock.txt",
        ):
            self.assertIn(expected_snapshot, linux_job)
        self.assertNotIn("git diff --exit-code", linux_job)

    def test_vercel_lock_runs_twice_and_checks_committed_output(self) -> None:
        workflow = _read(".github/workflows/ci.yml")
        vercel_job = workflow[
            workflow.index("  vercel-dependency-locks:") : workflow.index(
                "  windows-dependency-locks:"
            )
        ]
        compile_commands = re.findall(
            r"(?m)^\s+python -m piptools compile ",
            vercel_job,
        )
        self.assertEqual(len(compile_commands), 2)
        self.assertIn("cp requirements.txt /tmp/requirements.expected.txt", vercel_job)
        self.assertIn("sha256sum requirements.txt", vercel_job)
        self.assertIn(
            "cmp /tmp/requirements.expected.txt requirements.txt",
            vercel_job,
        )

    def test_first_party_actions_are_commit_pinned(self) -> None:
        for relative_path in (
            ".github/workflows/ci.yml",
            ".github/workflows/safe-baseline-release.yml",
        ):
            workflow = _read(relative_path)
            self.assertNotRegex(workflow, r"uses:\s*actions/[^@\s]+@v\d+")
            uses = re.findall(r"uses:\s*(actions/[^@\s]+)@([^\s#]+)", workflow)
            self.assertGreater(len(uses), 0)
            for action, revision in uses:
                with self.subTest(path=relative_path, action=action):
                    self.assertRegex(revision, r"^[0-9a-f]{40}$")


class SafeBaselineWorkflowContractTest(unittest.TestCase):
    def test_alembic_can_reuse_the_reservation_transaction_connection(self) -> None:
        env_source = _read("backend/alembic/env.py")
        self.assertIn('config.attributes.get("connection")', env_source)
        self.assertIn("do_run_migrations(existing_connection)", env_source)

    def test_safe_baseline_workflow_is_manual_protected_and_one_purpose(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        self.assertNotRegex(workflow, r"(?m)^\s+(push|pull_request|workflow_call):\s*$")
        self.assertIn("source_sha:", workflow)
        self.assertIn("required: true", workflow)
        self.assertIn("environment: production", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("permissions:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("runner_sha:", workflow)
        self.assertIn('test "$GITHUB_SHA" = "$RUNNER_SHA"', workflow)
        self.assertIn('git merge-base --is-ancestor "$SOURCE_SHA" "$RUNNER_SHA"', workflow)
        self.assertNotIn('--expected-sha "$SOURCE_SHA"', workflow)
        self.assertGreaterEqual(workflow.count('--expected-sha "$RUNNER_SHA"'), 6)
        self.assertGreaterEqual(workflow.count("verify_github_ref.py"), 6)
        self.assertNotIn("git ls-remote", workflow)
        self.assertNotIn("git fetch", workflow)
        self.assertNotIn("@latest", workflow)
        self.assertNotIn("npm install -g vercel", workflow)
        self.assertIn("--skip-domain", workflow)
        self.assertEqual(workflow.count("--force"), 1)
        self.assertNotRegex(workflow, r'"\$VERCEL_CLI" (?:deploy|promote)[^\n]*--force')

    def test_release_cli_is_installed_from_a_committed_integrity_lock(self) -> None:
        package = json.loads(_read("scripts/release-tools/package.json"))
        lock = json.loads(_read("scripts/release-tools/package-lock.json"))
        expected_security_overrides = {
            "@tootallnate/once@2.0.0": "2.0.1",
            "ajv@8.6.3": "8.18.0",
            "js-yaml@4.1.1": "4.2.0",
            "minimatch@10.1.1": "10.2.5",
            "path-to-regexp@6.1.0": "6.3.0",
            "path-to-regexp@8.2.0": "8.4.0",
            "path-to-regexp@8.3.0": "8.4.0",
            "sandbox@3.4.0": "3.4.3",
            "smol-toml@1.5.2": "1.6.1",
            "tar@7.5.7": "7.5.16",
            "undici@5.28.4": "6.27.0",
            "undici@5.29.0": "6.27.0",
        }
        self.assertEqual(package["devDependencies"]["vercel"], "56.2.0")
        self.assertEqual(package["devDependencies"]["@emnapi/core"], "1.11.2")
        self.assertEqual(package["devDependencies"]["@emnapi/runtime"], "1.11.2")
        self.assertEqual(package["overrides"], expected_security_overrides)
        self.assertEqual(lock["packages"][""]["devDependencies"]["vercel"], "56.2.0")
        self.assertEqual(
            lock["packages"][""]["devDependencies"]["@emnapi/core"],
            "1.11.2",
        )
        self.assertEqual(
            lock["packages"][""]["devDependencies"]["@emnapi/runtime"],
            "1.11.2",
        )
        self.assertRegex(
            lock["packages"]["node_modules/@emnapi/core"]["integrity"],
            r"^sha512-",
        )
        self.assertRegex(
            lock["packages"]["node_modules/@emnapi/runtime"]["integrity"],
            r"^sha512-",
        )
        self.assertEqual(lock["packages"]["node_modules/vercel"]["version"], "56.2.0")
        self.assertEqual(lock["packages"]["node_modules/@tootallnate/once"]["version"], "2.0.1")
        self.assertEqual(lock["packages"]["node_modules/ajv"]["version"], "8.18.0")
        self.assertEqual(lock["packages"]["node_modules/js-yaml"]["version"], "4.2.0")
        self.assertEqual(lock["packages"]["node_modules/minimatch"]["version"], "10.2.5")
        self.assertEqual(lock["packages"]["node_modules/path-to-regexp"]["version"], "8.4.0")
        self.assertEqual(lock["packages"]["node_modules/path-to-regexp-updated"]["version"], "6.3.0")
        self.assertEqual(lock["packages"]["node_modules/sandbox"]["version"], "3.4.3")
        self.assertEqual(lock["packages"]["node_modules/smol-toml"]["version"], "1.6.1")
        self.assertEqual(lock["packages"]["node_modules/tar"]["version"], "7.5.16")
        self.assertEqual(lock["packages"]["node_modules/undici"]["version"], "6.27.0")
        self.assertRegex(
            lock["packages"]["node_modules/vercel"]["integrity"],
            r"^sha512-",
        )
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        self.assertIn(
            "npm ci --prefix scripts/release-tools --ignore-scripts",
            workflow,
        )
        self.assertIn(
            'VERCEL_CLI="$GITHUB_WORKSPACE/scripts/release-tools/node_modules/.bin/vercel"',
            workflow,
        )
        self.assertIn(
            'test "$("$VERCEL_CLI" --version)" = "56.2.0"',
            workflow,
        )
        self.assertNotIn("npx --yes vercel", workflow)

    def test_preflight_inventory_reservation_and_verification_precede_deploy_secret(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        preflight = workflow.index("--action preflight")
        inventory = workflow.index("inventory_production.py", preflight)
        preflight_command = workflow[preflight:inventory]
        rehearsal = workflow.index("run_isolated_restore_rehearsal.sh", preflight)
        reservation = workflow.index("--phase RESERVED")
        deployment_secret = workflow.index("secrets.VERCEL_TOKEN", reservation)
        build = workflow.index('"$VERCEL_CLI" build')
        edge_lockdown = workflow.index("verify_edge_lockdown.py")
        self.assertLess(preflight, inventory)
        self.assertLess(preflight, rehearsal)
        self.assertLess(inventory, reservation)
        self.assertLess(rehearsal, reservation)
        self.assertLess(edge_lockdown, reservation)
        self.assertLess(reservation, deployment_secret)
        self.assertLess(edge_lockdown, deployment_secret)
        self.assertLess(deployment_secret, build)
        self.assertIn('--workflow-attempt "$GITHUB_RUN_ATTEMPT"', preflight_command)
        self.assertIn("build_runtime_bundle_id.py", workflow)
        self.assertIn("verify_safe_baseline.py", workflow)
        self.assertIn("register_safe_baseline.py --phase COMPLETED", workflow)

    def test_unbound_reservation_adoption_is_evidence_first_and_precedes_external_writes(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        preflight = workflow.index('"TAKEOVER_RESERVED"')
        edge_lockdown = workflow.index("verify_edge_lockdown.py", preflight)
        durable_evidence = workflow.index("id: reservation_evidence", edge_lockdown)
        main_recheck = workflow.index(
            "Recheck current main before migration, reservation, or staged verifier takeover",
            durable_evidence,
        )
        adoption = workflow.index("--action adopt-reserved", main_recheck)
        provisioning = workflow.index("provision_production_database_logins.py", adoption)
        build = workflow.index('"$VERCEL_CLI" build', provisioning)
        self.assertLess(preflight, edge_lockdown)
        self.assertLess(edge_lockdown, durable_evidence)
        self.assertLess(durable_evidence, main_recheck)
        self.assertLess(main_recheck, adoption)
        self.assertLess(adoption, provisioning)
        self.assertLess(provisioning, build)
        self.assertIn('"RETRY_RESERVED" if reservation_takeover_required', workflow)
        self.assertIn('else "RETRY_STAGED" if staged_verifier_takeover_required', workflow)
        self.assertIn("steps.preflight.outputs.expected_source_sha", workflow)
        self.assertIn("steps.preflight.outputs.expected_workflow_run_id", workflow)
        self.assertIn("steps.preflight.outputs.expected_version", workflow)
        self.assertIn("steps.reservation_evidence.outputs.artifact-url", workflow)

    def test_staged_verifier_takeover_is_evidence_first_and_preserves_runtime_source(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        preflight = workflow.index('"TAKEOVER_STAGED"')
        durable_evidence = workflow.index("id: reservation_evidence", preflight)
        main_recheck = workflow.index(
            "Recheck current main before migration, reservation, or staged verifier takeover",
            durable_evidence,
        )
        adoption = workflow.index("--action adopt-staged-verifier", main_recheck)
        staged_verification = workflow.index(
            "Verify the staged application and authenticated runtime DDL audit",
            adoption,
        )
        self.assertLess(preflight, durable_evidence)
        self.assertLess(durable_evidence, main_recheck)
        self.assertLess(main_recheck, adoption)
        self.assertLess(adoption, staged_verification)
        adoption_step = workflow[adoption:staged_verification]
        self.assertIn('--source-sha "$SOURCE_SHA"', adoption_step)
        self.assertIn('--runner-sha "$RUNNER_SHA"', adoption_step)
        self.assertNotIn("--expected-runner-sha", adoption_step)

    def test_restore_target_is_ephemeral_loopback_postgres_17(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        restore = _read("scripts/release/run_isolated_restore_rehearsal.sh")
        self.assertIn("postgresql-17", workflow)
        self.assertIn('PG_BIN="${PG_BIN:-/usr/lib/postgresql/17/bin}"', restore)
        self.assertIn('= "17"', workflow)
        self.assertIn("--auth-host=trust --auth-local=trust", restore)
        self.assertIn("scram-sha-256", restore)
        self.assertIn("127.0.0.1:$RESTORE_PORT", restore)
        self.assertIn("vowpic_restore_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}", restore)
        self.assertIn('RUNNER_TEMP_RESOLVED="$(realpath -m "$RUNNER_TEMP")"', restore)
        self.assertIn('"$RUNNER_TEMP_RESOLVED"/*', restore)
        self.assertIn('"$WORKSPACE_ARTIFACTS_RESOLVED"/*', restore)
        self.assertIn('-k $RESTORE_PGDATA', restore)
        self.assertIn('tail -n 80 "$RESTORE_PGLOG" >&2', restore)
        self.assertLess(
            restore.index('tail -n 80 "$RESTORE_PGLOG" >&2'),
            restore.index("ALTER ROLE postgres WITH PASSWORD"),
        )
        self.assertIn('rm -rf -- "$RESTORE_PGDATA" "$RESTORE_SCRATCH_DIR"', restore)
        self.assertIn('rm -f -- "$RESTORE_PGLOG"', restore)
        self.assertNotIn("secrets.RESTORE_TARGET_", workflow)
        self.assertNotIn(
            "--target-credential-expires-at-env RESTORE_TARGET_CREDENTIAL_EXPIRES_AT",
            workflow,
        )
        self.assertIn(
            "https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt",
            workflow,
        )
        self.assertIn(
            "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7",
            workflow,
        )
        self.assertIn(
            'echo "PGSSLROOTCERT=$RUNNER_TEMP/prod-ca-2021.crt" >> "$GITHUB_ENV"',
            workflow,
        )
        self.assertNotIn("SSL_CERT_FILE", workflow)

    def test_legacy_bridge_is_evidence_first_exact_and_idempotent(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        discovery = workflow.index("Discover the exact Production schema boundary")
        inventory_rls = workflow.index("production_inventory_rls.py")
        legacy_inventory = workflow.index("Inventory the legacy source")
        legacy_restore = workflow.index("Rehearse the legacy source")
        durable_evidence = workflow.index("id: legacy_evidence")
        bridge = workflow.index("alembic upgrade 20260516_0012")
        formal_preflight = workflow.index("--action preflight")
        self.assertLess(discovery, legacy_inventory)
        self.assertLess(discovery, inventory_rls)
        self.assertLess(inventory_rls, legacy_inventory)
        self.assertLess(legacy_inventory, legacy_restore)
        self.assertLess(legacy_restore, durable_evidence)
        self.assertLess(durable_evidence, bridge)
        self.assertLess(bridge, formal_preflight)
        self.assertIn(
            'allowed = {"20260427_0006", "20260516_0012", "20260712_0014"}',
            workflow,
        )
        self.assertIn("legacy bridge revision mismatch", workflow)
        self.assertIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)

    def test_application_logins_are_provisioned_before_the_first_build(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        reservation = workflow.index("--phase RESERVED")
        provisioning = workflow.index("provision_production_database_logins.py")
        build = workflow.index('"$VERCEL_CLI" build')
        self.assertLess(reservation, provisioning)
        self.assertLess(provisioning, build)
        step_start = workflow.rindex(
            "Provision and publish the two least-privilege application logins",
            0,
            provisioning,
        )
        step_end = workflow.index(
            "Configure and read back the protected Vercel automation bypass",
            provisioning,
        )
        provisioning_step = workflow[step_start:step_end]
        self.assertIn("secrets.PRODUCTION_MIGRATION_DATABASE_URL", provisioning_step)
        self.assertIn(
            "CLEANUP_CRON_TOKEN: ${{ secrets.CLEANUP_CRON_TOKEN }}",
            provisioning_step,
        )
        self.assertNotIn("secrets.DATABASE_URL", workflow)
        self.assertNotIn("secrets.CONTROL_PLANE_DATABASE_URL", workflow)

    def test_vercel_automation_bypass_is_configured_before_the_first_build(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        provisioning = workflow.index("provision_production_database_logins.py")
        bypass = workflow.index("ensure_vercel_automation_bypass.py")
        build = workflow.index('"$VERCEL_CLI" build')
        self.assertLess(provisioning, bypass)
        self.assertLess(bypass, build)
        self.assertIn(
            "VERCEL_AUTOMATION_BYPASS_HEADER: ${{ secrets.VERCEL_AUTOMATION_BYPASS_HEADER }}",
            workflow,
        )

    def test_runtime_ddl_evidence_is_collected_from_the_exact_deployment(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        staged_collect = workflow.index(
            "Collect the staged runtime DDL audit from PostgreSQL statistics"
        )
        identity_grant = workflow.index(
            "Prove the staged runtime identity membership before application probes"
        )
        staged_verify = workflow.index(
            "Verify the staged application and authenticated runtime DDL audit"
        )
        formal_collect = workflow.index(
            "Collect the formal-domain runtime DDL audit from PostgreSQL statistics"
        )
        formal_verify = workflow.index(
            "Hand off every edge group and verify the formal domain without a bypass"
        )
        self.assertLess(identity_grant, staged_collect)
        self.assertLess(staged_collect, staged_verify)
        self.assertLess(formal_collect, formal_verify)
        self.assertEqual(workflow.count("collect_runtime_ddl_audit.py"), 2)
        self.assertEqual(
            workflow.count('--request-origin "$PRODUCTION_BASE_URL"'),
            4,
        )
        self.assertIn("runtime-ddl-audit-staged.json", workflow)
        self.assertIn("runtime-ddl-audit-formal.json", workflow)
        self.assertNotIn("RUNTIME_DDL_AUDIT_REPORT_B64", workflow)

    def test_sanitized_attempt_evidence_is_preserved_without_dump_or_runner_temp(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        self.assertRegex(
            workflow,
            r"uses:\s*actions/upload-artifact@[0-9a-f]{40}",
        )
        self.assertIn("if: failure()", workflow)
        self.assertIn("path: artifacts/security-baseline", workflow)
        self.assertIn("Materialize sanitized early failure evidence", workflow)
        self.assertIn('evidence_dir / "early-failure.json"', workflow)
        self.assertLess(
            workflow.index("Materialize sanitized early failure evidence"),
            workflow.index("Preserve sanitized failure diagnostics"),
        )
        runner_temp_paths = re.findall(
            r"^\s*path:\s*(\$\{\{ runner\.temp \}\}[^\r\n]*)$",
            workflow,
            flags=re.MULTILINE,
        )
        self.assertEqual(
            runner_temp_paths,
            ["${{ runner.temp }}/build-artifact"],
        )
        self.assertIn("${{ runner.temp }}/safe-baseline-build.tar.enc", workflow)
        self.assertIn(
            "${{ runner.temp }}/safe-baseline-build.manifest.sha256.enc",
            workflow,
        )
        self.assertNotIn("restore.dump", workflow)
        self.assertNotIn("if-no-files-found: warn", workflow)
        self.assertGreaterEqual(workflow.count("if-no-files-found: error"), 4)

        reservation_evidence = workflow.index("id: reservation_evidence")
        reservation = workflow.index("--phase RESERVED")
        staged_evidence = workflow.index("id: staged_evidence")
        promotion = workflow.index("--action recover-promotion")
        formal_verification = workflow.index(
            '--output "$EVIDENCE_DIR/safe-baseline-formal.json"'
        )
        formal_evidence = workflow.index("id: formal_evidence")
        formal_cas = workflow.index("--phase FORMAL_VERIFIED")
        completion_evidence = workflow.index("id: completion_evidence")
        completed_cas = workflow.index("--phase COMPLETED")
        self.assertLess(reservation_evidence, reservation)
        self.assertLess(staged_evidence, promotion)
        self.assertLess(formal_verification, formal_evidence)
        self.assertLess(formal_evidence, formal_cas)
        self.assertLess(completion_evidence, completed_cas)
        for checkpoint in (
            "reservation_evidence",
            "staged_evidence",
            "formal_evidence",
            "completion_evidence",
        ):
            self.assertIn(f"steps.{checkpoint}.outputs.artifact-id", workflow)
            self.assertIn(f"steps.{checkpoint}.outputs.artifact-digest", workflow)
        self.assertIn(
            'steps.reservation_evidence.outputs.artifact-url',
            workflow,
        )

    def test_edge_lockdown_report_is_authenticated_and_bound_to_the_release(self) -> None:
        verify = _load_script(
            "scripts/release/verify_edge_lockdown.py",
            "verify_edge_lockdown_contract",
        )
        physical_rule_by_group = {
            "auth_upload": "rule_identity_generation",
            "generation": "rule_identity_generation",
            "partner_invite": "rule_identity_generation",
            "credit_checkout": "rule_commercial_retired",
            "subscription": "rule_commercial_retired",
            "retired_addons": "rule_commercial_retired",
            "leads_recommendations": "rule_commercial_retired",
        }
        now = datetime.now(timezone.utc)
        source_sha = "a" * 40
        payload = {
            "schema_version": "vowpic.edge-lockdown.v1",
            "passed": True,
            "project_id": "prj_vowpic",
            "source_sha": source_sha,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "formal_domain": "www.vowpic.com",
            "last_known_deployment_id": "dpl_last_known",
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "auto_assign_custom_production_domains": False,
            "active_deploy_hook_count": 0,
            "before_config_sha256": "b" * 64,
            "after_config_sha256": "c" * 64,
            "baseline_config_sha256": "d" * 64,
            "route_groups": {
                name: {
                    "rule_id": physical_rule_by_group[name],
                    "denied": True,
                    "read_back": True,
                }
                for name in sorted(verify.EDGE_ROUTE_GROUPS)
            },
            "runner_bypass": {
                "rule_id": "rule_runner_bypass",
                "lease_expires_at": (now + timedelta(minutes=20)).isoformat(),
                "read_back": True,
            },
        }
        key = b"k" * 32
        payload["signature_hmac_sha256"] = verify.compute_report_hmac(payload, key)
        verified = verify.validate_edge_lockdown_report(
            payload,
            hmac_key=key,
            expected_source_sha=source_sha,
            expected_workflow_run_id="12345",
            expected_workflow_attempt=2,
            expected_project_id="prj_vowpic",
            expected_formal_domain="www.vowpic.com",
            now=now,
        )
        self.assertEqual(verified["route_groups"], sorted(verify.EDGE_ROUTE_GROUPS))
        self.assertEqual(verified["physical_deny_rule_count"], 2)
        self.assertEqual(verify.MAX_PHYSICAL_EDGE_DENY_RULES, 2)
        runbook = _read("docs/operations/risk-lockdown-runbook.md")
        self.assertIn("Hobby physical-rule limit", runbook)
        self.assertIn(
            "one physical rule id may back multiple logical groups",
            re.sub(r"\s+", " ", runbook.lower()),
        )
        tampered = dict(payload, source_sha="d" * 40)
        with self.assertRaises(ValueError):
            verify.validate_edge_lockdown_report(
                tampered,
                hmac_key=key,
                expected_source_sha=source_sha,
                expected_workflow_run_id="12345",
                expected_workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_formal_domain="www.vowpic.com",
                now=now,
            )
        with self.assertRaises(ValueError):
            verify.validate_edge_lockdown_report(
                payload,
                hmac_key=key,
                expected_source_sha=source_sha,
                expected_workflow_run_id="12345",
                expected_workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_formal_domain="https://www.vowpic.com?unexpected=1",
                now=now,
            )
        too_many_physical_rules = {
            **payload,
            "route_groups": {
                name: {
                    "rule_id": f"rule_{index % 3}",
                    "denied": True,
                    "read_back": True,
                }
                for index, name in enumerate(sorted(verify.EDGE_ROUTE_GROUPS))
            },
        }
        too_many_physical_rules["signature_hmac_sha256"] = verify.compute_report_hmac(
            too_many_physical_rules,
            key,
        )
        with self.assertRaisesRegex(ValueError, "physical-rule limit"):
            verify.validate_edge_lockdown_report(
                too_many_physical_rules,
                hmac_key=key,
                expected_source_sha=source_sha,
                expected_workflow_run_id="12345",
                expected_workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_formal_domain="www.vowpic.com",
                now=now,
            )
        short_lease = {
            **payload,
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "runner_bypass": {
                **payload["runner_bypass"],
                "lease_expires_at": (now + timedelta(minutes=4)).isoformat(),
            },
        }
        short_lease["signature_hmac_sha256"] = verify.compute_report_hmac(short_lease, key)
        with self.assertRaises(ValueError):
            verify.validate_edge_lockdown_report(
                short_lease,
                hmac_key=key,
                expected_source_sha=source_sha,
                expected_workflow_run_id="12345",
                expected_workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_formal_domain="www.vowpic.com",
                now=now,
            )

    def test_safe_baseline_state_machine_rejects_orphans_skips_and_later_heads(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_contract",
        )
        sha = "a" * 40
        self.assertEqual(
            register.classify_install_state(
                current_revision="20260516_0012",
                activation=None,
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "FRESH_INSTALL",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=None,
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "ORPHANED_SCHEMA",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision="20260516_0012",
                activation={"source_sha": sha, "workflow_run_id": "run-1", "phase": "RESERVED"},
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "INVALID_RESERVATION_WITH_OLD_SCHEMA",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation={"source_sha": sha, "workflow_run_id": "run-1", "phase": "RESERVED"},
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "RETRY_RESERVED",
        )
        adoptable = {
            "source_sha": "b" * 40,
            "workflow_run_id": "run-0",
            "workflow_attempt": 1,
            "phase": "RESERVED",
            "version": 2,
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/1/artifacts/2",
        }
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=adoptable,
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "TAKEOVER_RESERVED",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation={**adoptable, "manifest_sha256": "d" * 64},
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "CONFLICTING_INSTALL",
        )
        bound_reserved = {
            **adoptable,
            "source_sha": sha,
            "workflow_run_id": "123",
            "approval": "protected-approval",
            "manifest_sha256": "d" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "e" * 64,
        }
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=bound_reserved,
                source_sha="b" * 40,
                workflow_run_id="789",
            ),
            "TAKEOVER_RESERVED_BUILD",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=bound_reserved,
                source_sha=sha,
                workflow_run_id="789",
            ),
            "TAKEOVER_RESERVED_CONTROL",
        )
        staged = {
            "source_sha": sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/2",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_exact",
            "api_deployment_url": "https://exact.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=staged,
                source_sha=sha,
                workflow_run_id="789",
            ),
            "TAKEOVER_STAGED",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation={**staged, "api_deployment_id": None},
                source_sha=sha,
                workflow_run_id="789",
            ),
            "CONFLICTING_INSTALL",
        )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation=staged,
                source_sha="b" * 40,
                workflow_run_id="789",
            ),
            "CONFLICTING_INSTALL",
        )
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        for phase in (
            "RESERVED",
            "STAGED",
            "PROMOTION_ARMED",
            "PROMOTED",
            "FORMAL_VERIFIED",
        ):
            with self.subTest(expired_phase=phase):
                self.assertEqual(
                    register.classify_install_state(
                        current_revision=register.TARGET_SCHEMA,
                        activation={
                            "source_sha": sha,
                            "workflow_run_id": "run-1",
                            "phase": phase,
                            "reservation_expires_at": expired_at,
                        },
                        source_sha=sha,
                        workflow_run_id="run-1",
                    ),
                    f"RETRY_{phase}",
                )
        created_at = datetime.now(timezone.utc) - timedelta(days=89)
        self.assertFalse(
            register.build_artifact_recovery_is_expired({"created_at": created_at})
        )
        self.assertTrue(
            register.build_artifact_recovery_is_expired(
                {"created_at": created_at - timedelta(days=1)}
            )
        )
        self.assertTrue(register.build_artifact_recovery_is_expired({}))
        register.validate_resume_coordinates(
            {
                "source_sha": sha,
                "workflow_run_id": "run-1",
                "workflow_attempt": 2,
            },
            source_sha=sha,
            workflow_run_id="run-1",
            workflow_attempt=3,
        )
        with self.assertRaises(ValueError):
            register.validate_resume_coordinates(
                {
                    "source_sha": sha,
                    "workflow_run_id": "run-1",
                    "workflow_attempt": 2,
                },
                source_sha=sha,
                workflow_run_id="run-1",
                workflow_attempt=1,
            )
        self.assertEqual(
            register.classify_install_state(
                current_revision=register.TARGET_SCHEMA,
                activation={"source_sha": "b" * 40, "workflow_run_id": "run-0", "phase": "COMPLETED"},
                source_sha=sha,
                workflow_run_id="run-1",
            ),
            "ALREADY_COMPLETED",
        )
        with self.assertRaises(ValueError):
            register.validate_phase_transition("RESERVED", "PROMOTED")
        register.validate_phase_transition("RESERVED", "STAGED")
        with self.assertRaises(ValueError):
            register.validate_phase_transition("STAGED", "PROMOTED")
        register.validate_phase_transition("STAGED", "PROMOTION_ARMED")
        register.validate_phase_transition("PROMOTION_ARMED", "PROMOTED")

    def test_unbound_reservation_adoption_uses_ancestry_approval_and_exact_cas(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_adoption_contract",
        )
        previous_source = "a" * 40
        source_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "RESERVED",
            "version": 2,
            "approval": "protected-approval",
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "current_snapshot_hash": "c" * 64,
        }
        connection = mock.MagicMock()
        update_result = {"id": activation["id"], "version": 3}
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = update_result
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection

        with (
            mock.patch.object(register, "validate_descendant_source") as ancestry,
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._adopt_reserved(
                "postgresql://migration",
                source_sha=source_sha,
                workflow_run_id="456",
                workflow_attempt=1,
                expected_source_sha=previous_source,
                expected_workflow_run_id="123",
                expected_version=2,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/456/artifacts/2",
            )

        ancestry.assert_called_once_with(previous_source, source_sha)
        self.assertEqual(result["state"], "RESERVATION_ADOPTED")
        self.assertEqual(result["previous_evidence_prefix"], activation["private_evidence_prefix"])
        statement = str(connection.execute.call_args.args[0])
        self.assertIn("version = :expected_version", statement)
        self.assertIn("phase = 'RESERVED'", statement)
        self.assertIn("manifest_sha256 IS NULL", statement)
        self.assertIn("api_deployment_id IS NULL", statement)
        self.assertIn("acceptance_fault_state IS NULL", statement)

    def test_bound_reserved_control_takeover_preserves_the_exact_build(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_bound_reserved_control_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 2,
            "phase": "RESERVED",
            "version": 26,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "manifest_sha256": "d" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "e" * 64,
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 27,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        runtime_secret_evidence = Path("runtime-secret-proof.json")

        with (
            mock.patch.object(
                register,
                "validate_staged_control_descendant",
            ) as control_diff,
            mock.patch.object(
                register,
                "_validate_runtime_secret_evidence",
            ) as runtime_secret_proof,
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_configure_migration_timeouts") as timeouts,
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(
                register,
                "_current_revision",
                return_value=register.TARGET_SCHEMA,
            ),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._adopt_bound_reserved_control(
                "postgresql://migration",
                source_sha=source_sha,
                runner_sha=runner_sha,
                workflow_run_id="789",
                workflow_attempt=1,
                expected_source_sha=source_sha,
                expected_workflow_run_id="123",
                expected_version=26,
                expected_manifest_sha256="d" * 64,
                expected_build_artifact_id="456",
                expected_build_artifact_digest="sha256:" + "e" * 64,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
                runtime_secret_evidence=runtime_secret_evidence,
                project_id="prj_project",
                team_id="team_owner",
            )

        control_diff.assert_called_once_with(source_sha, runner_sha)
        runtime_secret_proof.assert_called_once_with(
            runtime_secret_evidence,
            source_sha=source_sha,
            runner_sha=runner_sha,
            workflow_run_id="789",
            workflow_attempt=1,
            project_id="prj_project",
            team_id="team_owner",
        )
        timeouts.assert_called_once_with(connection)
        self.assertEqual(result["state"], "BOUND_RESERVED_CONTROL_ADOPTED")
        self.assertEqual(result["manifest_sha256"], "d" * 64)
        self.assertEqual(result["build_artifact_id"], "456")
        statement = str(connection.execute.call_args.args[0])
        self.assertNotIn("SET source_sha", statement)
        self.assertNotIn("manifest_sha256 = NULL", statement)
        self.assertNotIn("build_artifact_id = NULL", statement)
        self.assertIn("workflow_run_id = :workflow_run_id", statement)
        self.assertIn(
            "manifest_sha256 = :expected_manifest_sha256",
            statement,
        )
        self.assertIn(
            "build_artifact_id = :expected_build_artifact_id",
            statement,
        )
        self.assertIn("api_deployment_id IS NULL", statement)

    def test_staged_verifier_takeover_updates_only_run_ownership_and_evidence(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_takeover_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_exact",
            "api_deployment_url": "https://exact.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 5,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection

        with (
            mock.patch.object(register, "validate_staged_control_descendant") as control_diff,
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._adopt_staged_verifier(
                "postgresql://migration",
                source_sha=source_sha,
                runner_sha=runner_sha,
                workflow_run_id="789",
                workflow_attempt=1,
                expected_source_sha=source_sha,
                expected_workflow_run_id="123",
                expected_version=4,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
            )

        control_diff.assert_called_once_with(source_sha, runner_sha)
        self.assertEqual(result["state"], "STAGED_VERIFIER_ADOPTED")
        self.assertEqual(result["source_sha"], source_sha)
        self.assertEqual(result["runner_sha"], runner_sha)
        statement = str(connection.execute.call_args.args[0])
        self.assertNotIn("SET source_sha", statement)
        self.assertIn("workflow_run_id = :workflow_run_id", statement)
        self.assertIn("private_evidence_prefix = :evidence_prefix", statement)
        self.assertIn("phase = 'STAGED'", statement)
        self.assertIn("runtime_bundle_id IS NOT NULL", statement)
        self.assertIn("report_sha256 IS NULL", statement)

    def test_sensitive_runtime_secret_proof_never_contains_secret_or_fingerprint(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_runtime_secret_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        protected_secret = "k" * 64
        fingerprint = hashlib.sha256(protected_secret.encode("utf-8")).hexdigest()
        response = mock.MagicMock()
        response.json.return_value = [
            {
                "id": "env_acceptance_identity",
                "key": "ACCEPTANCE_IDENTITY_HMAC_KEY",
                "type": "sensitive",
                "target": ["production"],
                "gitBranch": None,
                "customEnvironmentIds": [],
                "decrypted": False,
            }
        ]
        with tempfile.TemporaryDirectory(prefix="vowpic-runtime-secret-") as directory:
            env_file = Path(directory, ".env.production.local")
            env_file.write_text(
                f'ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256="{fingerprint}"\n',
                encoding="utf-8",
            )
            with mock.patch.object(register.httpx, "get", return_value=response) as vercel_get:
                evidence = register.verify_sensitive_runtime_secret_proof(
                    env_file,
                    secret_name="ACCEPTANCE_IDENTITY_HMAC_KEY",
                    fingerprint_name="ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
                    expected_secret=protected_secret,
                    vercel_token="vercel-token",
                    project_id="prj_project",
                    team_id="team_owner",
                    source_sha=source_sha,
                    runner_sha=runner_sha,
                    workflow_run_id="123",
                    workflow_attempt=1,
                )
            self.assertTrue(evidence["passed"])
            self.assertEqual(evidence["minimum_length"], 32)
            serialized = json.dumps(evidence, sort_keys=True)
            self.assertNotIn(protected_secret, serialized)
            self.assertNotIn(fingerprint, serialized)
            self.assertFalse(evidence["vercel_value_readable"])
            self.assertEqual(evidence["vercel_secret_type"], "sensitive")
            vercel_get.assert_called_once_with(
                "https://api.vercel.com/v10/projects/prj_project/env",
                params={"teamId": "team_owner", "target": "production"},
                headers={"Authorization": "Bearer vercel-token"},
                timeout=30.0,
                follow_redirects=False,
            )
            with self.assertRaisesRegex(ValueError, "do not match"):
                register.verify_sensitive_runtime_secret_proof(
                    env_file,
                    secret_name="ACCEPTANCE_IDENTITY_HMAC_KEY",
                    fingerprint_name="ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
                    expected_secret="z" * 64,
                    vercel_token="vercel-token",
                    project_id="prj_project",
                    team_id="team_owner",
                    source_sha=source_sha,
                    runner_sha=runner_sha,
                    workflow_run_id="123",
                    workflow_attempt=1,
                )

            invalid_records = (
                {**response.json.return_value[0], "type": "encrypted"},
                {**response.json.return_value[0], "target": ["preview", "production"]},
                {**response.json.return_value[0], "gitBranch": "main"},
                {**response.json.return_value[0], "customEnvironmentIds": ["env_custom"]},
                {**response.json.return_value[0], "decrypted": True},
            )
            for record in invalid_records:
                with self.subTest(record=record):
                    invalid_response = mock.MagicMock()
                    invalid_response.json.return_value = [record]
                    with (
                        mock.patch.object(register.httpx, "get", return_value=invalid_response),
                        self.assertRaisesRegex(ValueError, "not Sensitive and Production-only"),
                    ):
                        register.verify_sensitive_runtime_secret_proof(
                            env_file,
                            secret_name="ACCEPTANCE_IDENTITY_HMAC_KEY",
                            fingerprint_name="ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
                            expected_secret=protected_secret,
                            vercel_token="vercel-token",
                            project_id="prj_project",
                            team_id="team_owner",
                            source_sha=source_sha,
                            runner_sha=runner_sha,
                            workflow_run_id="123",
                            workflow_attempt=1,
                        )
            for records in ([], response.json.return_value * 2):
                with self.subTest(record_count=len(records)):
                    invalid_response = mock.MagicMock()
                    invalid_response.json.return_value = records
                    with (
                        mock.patch.object(register.httpx, "get", return_value=invalid_response),
                        self.assertRaisesRegex(ValueError, "metadata is not unique"),
                    ):
                        register.verify_sensitive_runtime_secret_proof(
                            env_file,
                            secret_name="ACCEPTANCE_IDENTITY_HMAC_KEY",
                            fingerprint_name="ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
                            expected_secret=protected_secret,
                            vercel_token="vercel-token",
                            project_id="prj_project",
                            team_id="team_owner",
                            source_sha=source_sha,
                            runner_sha=runner_sha,
                            workflow_run_id="123",
                            workflow_attempt=1,
                        )

    def test_invalid_reserved_build_rearm_is_exact_cas_and_never_clears_a_deployment(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_build_rearm_contract",
        )
        previous_source_sha = "a" * 40
        source_sha = "b" * 40
        runner_sha = source_sha
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 2,
            "phase": "RESERVED",
            "version": 12,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "manifest_sha256": "d" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "e" * 64,
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 13,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(
                register,
                "validate_reserved_build_repair_descendant",
            ) as repair_diff,
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_configure_migration_timeouts") as configure_timeouts,
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._rearm_invalid_reserved_build(
                "postgresql://migration",
                source_sha=source_sha,
                runner_sha=runner_sha,
                workflow_run_id="789",
                workflow_attempt=1,
                expected_source_sha=previous_source_sha,
                expected_workflow_run_id="123",
                expected_version=12,
                expected_build_artifact_id="456",
                expected_build_artifact_digest="sha256:" + "e" * 64,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
            )

        repair_diff.assert_called_once_with(previous_source_sha, source_sha)
        self.assertEqual(result["state"], "RESERVED_BUILD_REARMED")
        self.assertEqual(result["source_sha"], source_sha)
        self.assertEqual(result["previous_source_sha"], previous_source_sha)
        self.assertEqual(result["next_workflow_attempt"], 2)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        disable_index = next(
            index for index, statement in enumerate(statements)
            if "DISABLE TRIGGER trg_release_activation_regression" in statement
        )
        update_index = next(
            index for index, statement in enumerate(statements)
            if "UPDATE release_activations" in statement
        )
        enable_index = next(
            index for index, statement in enumerate(statements)
            if "ENABLE TRIGGER trg_release_activation_regression" in statement
        )
        self.assertLess(disable_index, update_index)
        self.assertLess(update_index, enable_index)
        update = statements[update_index]
        self.assertIn("phase = 'RESERVED'", update)
        self.assertIn("SET source_sha = :source_sha", update)
        self.assertIn("source_sha = :expected_source_sha", update)
        self.assertIn("runtime_bundle_id IS NULL", update)
        self.assertIn("manifest_sha256 = NULL", update)
        self.assertIn("build_artifact_id = NULL", update)
        self.assertIn("api_deployment_id IS NULL", update)
        self.assertNotIn("api_deployment_id = NULL", update)
        self.assertIn("version = :expected_version", update)
        configure_timeouts.assert_called_once_with(connection)
        self.assertRegex(result["previous_coordinates_sha256"], r"^[0-9a-f]{64}$")

        for changed in (
            {"api_deployment_id": "dpl_existing", "api_deployment_url": "https://x.vercel.app"},
            {"runtime_bundle_id": "rtb_" + "f" * 64},
            {"report_sha256": "f" * 64},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(register.reserved_build_rearm_is_adoptable({**activation, **changed}))

    def test_bound_reserved_build_preflight_accepts_only_a_control_descendant(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_build_preflight_contract",
        )
        previous_source_sha = "a" * 40
        source_sha = "b" * 40
        runner_sha = source_sha
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 2,
            "phase": "RESERVED",
            "version": 12,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "manifest_sha256": "d" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "e" * 64,
        }
        connection = mock.MagicMock()
        connection.execute.return_value.scalar_one.return_value = "on"
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_reserved_build_repair_descendant",
            ) as repair_diff,
        ):
            report = register._preflight(
                "postgresql://read-only",
                source_sha=source_sha,
                runner_sha=runner_sha,
                workflow_run_id="789",
                workflow_attempt=1,
            )

        self.assertEqual(report["state"], "TAKEOVER_RESERVED_BUILD")
        repair_diff.assert_called_once_with(previous_source_sha, source_sha)

    def test_bound_reserved_control_preflight_requires_the_control_allowlist(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_bound_reserved_control_preflight_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 2,
            "phase": "RESERVED",
            "version": 26,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "manifest_sha256": "d" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "e" * 64,
        }
        connection = mock.MagicMock()
        connection.execute.return_value.scalar_one.return_value = "on"
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = connection

        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(
                register,
                "_current_revision",
                return_value=register.TARGET_SCHEMA,
            ),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_staged_control_descendant",
            ) as control_diff,
        ):
            report = register._preflight(
                "postgresql://read-only",
                source_sha=source_sha,
                runner_sha=runner_sha,
                workflow_run_id="789",
                workflow_attempt=1,
            )

        self.assertEqual(report["state"], "TAKEOVER_RESERVED_CONTROL")
        control_diff.assert_called_once_with(source_sha, runner_sha)

    def test_bound_reserved_prebuild_repair_is_explicit_evidence_first_and_rerun_fenced(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn("rearm_reserved_after_prebuilt_output_repair", workflow)
        self.assertIn('"TAKEOVER_RESERVED_BUILD"', workflow)
        self.assertIn('"rearm-reserved-build"', register_source)
        durable_evidence = workflow.index("id: reservation_evidence")
        main_recheck = workflow.index(
            "Recheck current main before migration, reservation, or staged verifier takeover",
            durable_evidence,
        )
        guard = workflow.index(
            "Fail closed unless the bound RESERVED prebuild repair was explicitly selected",
            main_recheck,
        )
        rearm = workflow.index("Atomically rearm the invalid bound RESERVED prebuild", guard)
        provisioning = workflow.index("Provision and publish the two least-privilege", rearm)
        self.assertLess(durable_evidence, main_recheck)
        self.assertLess(main_recheck, guard)
        self.assertLess(guard, rearm)
        self.assertLess(rearm, provisioning)
        step = workflow[rearm:provisioning]
        self.assertIn("steps.preflight.outputs.activation_build_artifact_id", step)
        self.assertIn("steps.preflight.outputs.activation_build_artifact_digest", step)
        self.assertIn("exit 75", step)

    def test_bound_reserved_control_takeover_is_evidence_first_and_preserves_build(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn('"TAKEOVER_RESERVED_CONTROL"', workflow)
        self.assertIn('"adopt-reserved-control"', register_source)
        durable_evidence = workflow.index("id: reservation_evidence")
        main_recheck = workflow.index(
            "Recheck current main before migration, reservation, or staged verifier takeover",
            durable_evidence,
        )
        guard = workflow.index(
            "Fail closed unless the bound RESERVED control takeover was explicitly selected",
            main_recheck,
        )
        adoption = workflow.index(
            "Atomically transfer the exact bound RESERVED build to the reviewed controller",
            guard,
        )
        adoption_end = workflow.index(
            "Fail closed unless the bound RESERVED prebuild repair was explicitly selected",
            adoption,
        )
        provisioning = workflow.index(
            "Provision and publish the two least-privilege",
            adoption_end,
        )
        self.assertLess(durable_evidence, main_recheck)
        self.assertLess(main_recheck, guard)
        self.assertLess(guard, adoption)
        self.assertLess(adoption, provisioning)
        step = workflow[adoption:adoption_end]
        self.assertIn("--action adopt-reserved-control", step)
        self.assertIn("activation_manifest_sha256", step)
        self.assertIn("activation_build_artifact_id", step)
        self.assertIn("activation_build_artifact_digest", step)
        self.assertIn("staged-rearm-runtime-secret.json", step)
        self.assertNotIn("exit 75", step)
        self.assertNotIn("activation_artifact_workflow_run_id", workflow)
        self.assertNotIn("activation_artifact_workflow_attempt", workflow)
        self.assertIn("id: bound_build_lookup", workflow)
        self.assertIn("github_artifact_evidence.py lookup-bound-build", workflow)
        self.assertIn(
            '"backend/tests/test_release_coordinate_resolver.py"',
            register_source,
        )
        self.assertIn(
            '"scripts/release/github_artifact_evidence.py"',
            register_source,
        )
        self.assertIn(
            "--artifact-id \"$BOUND_BUILD_ARTIFACT_ID\"",
            workflow,
        )
        self.assertIn(
            "--artifact-digest \"$BOUND_BUILD_ARTIFACT_DIGEST\"",
            workflow,
        )
        self.assertIn(
            "BOUND_BUILD_WORKFLOW_RUN_ID: "
            "${{ steps.bound_build_lookup.outputs.workflow_run_id }}",
            workflow,
        )
        self.assertIn(
            "BOUND_BUILD_WORKFLOW_ATTEMPT: "
            "${{ steps.bound_build_lookup.outputs.workflow_attempt }}",
            workflow,
        )
        bound_lookup = workflow.index("id: bound_build_lookup")
        build_attempt = workflow.index("id: build_attempt", bound_lookup)
        build_lookup = workflow.index("id: build_lookup", build_attempt)
        build_download = workflow.index("id: build_download", build_lookup)
        self.assertLess(bound_lookup, build_attempt)
        self.assertLess(build_attempt, build_lookup)
        self.assertLess(build_lookup, build_download)
        self.assertIn(
            'test "$BOUND_BUILD_STATE" = "FOUND"',
            workflow[build_attempt:build_lookup],
        )
        self.assertIn(
            "--run-id \"${{ steps.build_attempt.outputs.workflow_run_id }}\"",
            workflow,
        )
        self.assertIn(
            "run-id: ${{ steps.build_attempt.outputs.workflow_run_id }}",
            workflow,
        )
        self.assertIn(
            "vowpic-safe-baseline-build:v2:${{ inputs.source_sha }}:"
            "${{ steps.build_attempt.outputs.workflow_run_id }}:"
            "${{ steps.build_attempt.outputs.workflow_attempt }}",
            workflow,
        )
        self.assertIn(
            'test "$BUILD_ARTIFACT_ID" = "$EXPECTED_BUILD_ARTIFACT_ID"',
            workflow,
        )
        self.assertIn(
            'test "$BUILD_ARTIFACT_DIGEST" = "$EXPECTED_BUILD_ARTIFACT_DIGEST"',
            workflow,
        )
        self.assertIn("bound_reserved_control_retry_allowed = (", workflow)
        self.assertIn(
            'activation_workflow_run_id == os.environ["GITHUB_RUN_ID"]',
            workflow,
        )
        self.assertIn(
            '0 < int(activation_attempt or "0")',
            workflow,
        )
        self.assertIn(
            '< int(os.environ["GITHUB_RUN_ATTEMPT"])',
            workflow,
        )
        self.assertIn(
            "steps.preflight.outputs.bound_reserved_control_retry_allowed != 'true'",
            workflow,
        )
        self.assertIn(
            're.fullmatch(r"[1-9][0-9]{0,19}", activation_build_artifact_id)',
            workflow,
        )
        self.assertIn(
            'r"sha256:[0-9a-f]{64}"',
            workflow,
        )
        self.assertIn('and not activation.get("api_deployment_id")', workflow)

    def test_staged_runtime_tls_repair_diff_is_exact_and_pinned(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_runtime_tls_diff_contract",
        )
        previous_source_sha = register.STAGED_RUNTIME_TLS_REPAIR_PREVIOUS_SOURCE_SHA
        source_sha = "b" * 40
        diff_lines = []
        for path in sorted(register.STAGED_RUNTIME_TLS_REPAIR_REQUIRED_PATHS):
            status = "A" if path == "backend/app/core/certs/prod-ca-2021.crt" else "M"
            diff_lines.append(f"{status}\t{path}")
        subprocess_results = [
            subprocess.CompletedProcess([], 0, stdout=source_sha + "\n"),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout="\n".join(diff_lines) + "\n"),
        ]
        with mock.patch.object(register.subprocess, "run", side_effect=subprocess_results):
            register.validate_staged_runtime_tls_repair_descendant(
                previous_source_sha,
                source_sha,
            )

        unauthorized_results = [
            subprocess.CompletedProcess([], 0, stdout=source_sha + "\n"),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="\n".join([*diff_lines, "M\tbackend/app/main.py"]) + "\n",
            ),
        ]
        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=unauthorized_results,
            ),
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "unauthorized path",
            ),
        ):
            register.validate_staged_runtime_tls_repair_descendant(
                previous_source_sha,
                source_sha,
            )

    def test_staged_runtime_tls_repair_preflight_is_fenced_to_one_descendant(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_runtime_tls_preflight_contract",
        )
        previous_source_sha = register.STAGED_RUNTIME_TLS_REPAIR_PREVIOUS_SOURCE_SHA
        source_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_invalid_tls",
            "api_deployment_url": "https://invalid-tls.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.scalar_one.return_value = "on"
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_staged_runtime_tls_repair_descendant",
            ) as repair_diff,
        ):
            report = register._preflight(
                "postgresql://read-only",
                source_sha=source_sha,
                runner_sha=source_sha,
                workflow_run_id="789",
                workflow_attempt=1,
            )

        self.assertEqual(report["state"], "TAKEOVER_STAGED_RUNTIME_TLS")
        repair_diff.assert_called_once_with(previous_source_sha, source_sha)

    def test_invalid_staged_runtime_tls_rearm_advances_source_and_clears_binding(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_runtime_tls_rearm_contract",
        )
        previous_source_sha = register.STAGED_RUNTIME_TLS_REPAIR_PREVIOUS_SOURCE_SHA
        source_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_invalid_tls",
            "api_deployment_url": "https://invalid-tls.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 5,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(
                register,
                "validate_staged_runtime_tls_repair_descendant",
            ) as repair_diff,
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_configure_migration_timeouts"),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._rearm_invalid_staged(
                "postgresql://migration",
                source_sha=source_sha,
                runner_sha=source_sha,
                workflow_run_id="789",
                workflow_attempt=1,
                expected_source_sha=previous_source_sha,
                expected_workflow_run_id="123",
                expected_version=4,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
                runtime_secret_evidence=None,
                project_id="",
                team_id="",
                runtime_tls_repair=True,
            )

        repair_diff.assert_called_once_with(previous_source_sha, source_sha)
        self.assertEqual(result["state"], "STAGED_REARMED")
        self.assertEqual(result["previous_source_sha"], previous_source_sha)
        self.assertEqual(result["source_sha"], source_sha)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        update_index = next(
            index for index, statement in enumerate(statements)
            if "UPDATE release_activations" in statement
        )
        update = statements[update_index]
        update_params = connection.execute.call_args_list[update_index].args[1]
        self.assertIn("SET source_sha = :source_sha", update)
        self.assertIn("source_sha = :expected_source_sha", update)
        self.assertEqual(update_params["source_sha"], source_sha)
        self.assertEqual(update_params["expected_source_sha"], previous_source_sha)
        self.assertIn("phase = 'RESERVED'", update)
        self.assertIn("api_deployment_id = NULL", update)

    def test_staged_runtime_tls_repair_is_explicit_evidence_first_and_rerun_fenced(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn("rearm_staged_after_runtime_tls_repair", workflow)
        self.assertIn('"TAKEOVER_STAGED_RUNTIME_TLS"', workflow)
        self.assertIn("--runtime-tls-repair", workflow)
        self.assertIn(
            "Reject a runtime TLS repair input outside its exact STAGED takeover",
            workflow,
        )
        self.assertIn(
            'state == "RETRY_RESERVED"',
            workflow,
        )
        self.assertIn(
            'activation_attempt == os.environ["GITHUB_RUN_ATTEMPT"]',
            workflow,
        )
        self.assertIn(
            'int(activation_attempt or "0") > 1',
            workflow,
        )
        self.assertEqual(
            workflow.count(
                "steps.preflight.outputs.staged_rearm_retry_allowed != 'true'"
            ),
            4,
        )
        self.assertIn(
            "--runtime-tls-repair is valid only with --action rearm-staged",
            register_source,
        )
        self.assertIn(
            "9868401e52024fc347bb23ad0bca98858a2901f1",
            register_source,
        )
        durable_evidence = workflow.index("id: reservation_evidence")
        main_recheck = workflow.index(
            "Recheck current main before migration, reservation, or staged verifier takeover",
            durable_evidence,
        )
        guard = workflow.index(
            "Fail closed unless the exact STAGED runtime TLS repair was explicitly selected",
            main_recheck,
        )
        rearm = workflow.index(
            "Atomically rearm the invalid STAGED runtime after the reviewed TLS repair",
            guard,
        )
        config_rearm = workflow.index(
            "Atomically rearm the invalid unpromoted STAGED binding",
            rearm,
        )
        self.assertLess(durable_evidence, main_recheck)
        self.assertLess(main_recheck, guard)
        self.assertLess(guard, rearm)
        self.assertLess(rearm, config_rearm)

    def test_staged_schema_compatibility_repair_diff_is_exact_and_pinned(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_schema_compatibility_diff_contract",
        )
        previous_source_sha = (
            register.STAGED_SCHEMA_COMPATIBILITY_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "b" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(
                register.STAGED_SCHEMA_COMPATIBILITY_REPAIR_REQUIRED_PATHS
            )
        ) + "\n"

        def results(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n"),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout=diff),
            ]

        with mock.patch.object(
            register.subprocess,
            "run",
            side_effect=results(allowed_diff),
        ):
            register.validate_staged_schema_compatibility_repair_descendant(
                previous_source_sha,
                source_sha,
            )

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=results(allowed_diff + "M\tbackend/app/main.py\n"),
            ),
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "unauthorized path",
            ),
        ):
            register.validate_staged_schema_compatibility_repair_descendant(
                previous_source_sha,
                source_sha,
            )

    def test_staged_schema_compatibility_preflight_is_one_source_changing_takeover(
        self,
    ) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_schema_compatibility_preflight_contract",
        )
        previous_source_sha = (
            register.STAGED_SCHEMA_COMPATIBILITY_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": (
                "https://github.com/o/r/actions/runs/123/artifacts/1"
            ),
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_pre_identity_failure",
            "api_deployment_url": "https://pre-identity-failure.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.scalar_one.return_value = "on"
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(
                register,
                "_current_revision",
                return_value=register.TARGET_SCHEMA,
            ),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_staged_schema_compatibility_repair_descendant",
            ) as repair_diff,
        ):
            report = register._preflight(
                "postgresql://read-only",
                source_sha=source_sha,
                runner_sha=source_sha,
                workflow_run_id="789",
                workflow_attempt=1,
            )

        self.assertEqual(
            report["state"],
            "TAKEOVER_STAGED_SCHEMA_COMPATIBILITY",
        )
        repair_diff.assert_called_once_with(previous_source_sha, source_sha)

    def test_staged_schema_compatibility_repair_is_explicit_and_rerun_fenced(
        self,
    ) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn(
            "rearm_staged_after_schema_compatibility_repair",
            workflow,
        )
        self.assertIn('"TAKEOVER_STAGED_SCHEMA_COMPATIBILITY"', workflow)
        self.assertIn("--runtime-schema-compatibility-repair", workflow)
        self.assertIn(
            "Fail closed unless the exact STAGED schema-compatibility repair "
            "was explicitly selected",
            workflow,
        )
        self.assertIn(
            "--runtime-schema-compatibility-repair is valid only with ",
            register_source,
        )
        self.assertIn('"--action rearm-staged"', register_source)
        self.assertIn(
            "2d7ba47935a87ce4b2749554494c6abae92f07f3",
            register_source,
        )
        guard = workflow.index(
            "Fail closed unless the exact STAGED schema-compatibility repair "
            "was explicitly selected"
        )
        rearm = workflow.index(
            "Atomically rearm the invalid STAGED runtime after the "
            "schema-compatibility repair",
            guard,
        )
        config_rearm = workflow.index(
            "Atomically rearm the invalid unpromoted STAGED binding",
            rearm,
        )
        self.assertLess(guard, rearm)
        self.assertLess(rearm, config_rearm)
        step = workflow[rearm:config_rearm]
        self.assertIn("--expected-source-sha", step)
        self.assertIn("--expected-workflow-run-id", step)
        self.assertIn("--expected-version", step)
        self.assertIn("exit 75", step)

    def test_staged_route_compatibility_repair_diff_is_exact_and_pinned(
        self,
    ) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_route_compatibility_diff_contract",
        )
        previous_source_sha = (
            register.STAGED_ROUTE_COMPATIBILITY_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "b" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(
                register.STAGED_ROUTE_COMPATIBILITY_REPAIR_REQUIRED_PATHS
            )
        ) + "\n"

        def results(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n"),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout=diff),
            ]

        with mock.patch.object(
            register.subprocess,
            "run",
            side_effect=results(allowed_diff),
        ):
            register.validate_staged_route_compatibility_repair_descendant(
                previous_source_sha,
                source_sha,
            )

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=results(allowed_diff + "M\tbackend/app/main.py\n"),
            ),
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "unauthorized path",
            ),
        ):
            register.validate_staged_route_compatibility_repair_descendant(
                previous_source_sha,
                source_sha,
            )

    def test_staged_route_compatibility_preflight_and_rearm_are_source_fenced(
        self,
    ) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_route_compatibility_rearm_contract",
        )
        previous_source_sha = (
            register.STAGED_ROUTE_COMPATIBILITY_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": previous_source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": (
                "https://github.com/o/r/actions/runs/123/artifacts/1"
            ),
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_invalid_route",
            "api_deployment_url": "https://invalid-route.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        read_connection = mock.MagicMock()
        read_connection.execute.return_value.scalar_one.return_value = "on"
        read_engine = mock.MagicMock()
        read_engine.connect.return_value.__enter__.return_value = read_connection
        with (
            mock.patch.object(register, "create_engine", return_value=read_engine),
            mock.patch.object(
                register,
                "_current_revision",
                return_value=register.TARGET_SCHEMA,
            ),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_staged_route_compatibility_repair_descendant",
            ) as repair_diff,
        ):
            report = register._preflight(
                "postgresql://read-only",
                source_sha=source_sha,
                runner_sha=source_sha,
                workflow_run_id="789",
                workflow_attempt=1,
            )
        self.assertEqual(
            report["state"],
            "TAKEOVER_STAGED_ROUTE_COMPATIBILITY",
        )
        repair_diff.assert_called_once_with(previous_source_sha, source_sha)

        write_connection = mock.MagicMock()
        write_connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 5,
        }
        write_engine = mock.MagicMock()
        write_engine.begin.return_value.__enter__.return_value = write_connection
        with (
            mock.patch.object(register, "create_engine", return_value=write_engine),
            mock.patch.object(register, "_configure_migration_timeouts"),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(
                register,
                "_current_revision",
                return_value=register.TARGET_SCHEMA,
            ),
            mock.patch.object(register, "_read_activation", return_value=activation),
            mock.patch.object(
                register,
                "validate_staged_route_compatibility_repair_descendant",
            ) as repair_diff,
        ):
            result = register._rearm_invalid_staged(
                "postgresql://migration",
                source_sha=source_sha,
                runner_sha=source_sha,
                workflow_run_id="789",
                workflow_attempt=1,
                expected_source_sha=previous_source_sha,
                expected_workflow_run_id="123",
                expected_version=4,
                approval="protected-approval",
                evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
                runtime_secret_evidence=None,
                project_id="",
                team_id="",
                runtime_route_compatibility_repair=True,
            )
        repair_diff.assert_called_once_with(previous_source_sha, source_sha)
        self.assertEqual(result["state"], "STAGED_REARMED")
        self.assertEqual(result["previous_source_sha"], previous_source_sha)
        self.assertEqual(result["source_sha"], source_sha)

    def test_staged_route_compatibility_repair_is_explicit_and_rerun_fenced(
        self,
    ) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn(
            "rearm_staged_after_route_compatibility_repair",
            workflow,
        )
        self.assertIn('"TAKEOVER_STAGED_ROUTE_COMPATIBILITY"', workflow)
        self.assertIn("--runtime-route-compatibility-repair", workflow)
        self.assertIn(
            "Fail closed unless the exact STAGED route-compatibility repair "
            "was explicitly selected",
            workflow,
        )
        self.assertIn(
            "--runtime-route-compatibility-repair is valid only with ",
            register_source,
        )
        self.assertIn(
            "55eaeeea0748a96c7d040d9465bd64dd9bfbfd2e",
            register_source,
        )
        guard = workflow.index(
            "Fail closed unless the exact STAGED route-compatibility repair "
            "was explicitly selected"
        )
        rearm = workflow.index(
            "Atomically rearm the invalid STAGED runtime after the "
            "route-compatibility repair",
            guard,
        )
        config_rearm = workflow.index(
            "Atomically rearm the invalid unpromoted STAGED binding",
            rearm,
        )
        self.assertLess(guard, rearm)
        self.assertLess(rearm, config_rearm)
        step = workflow[rearm:config_rearm]
        self.assertIn("--expected-source-sha", step)
        self.assertIn("--expected-workflow-run-id", step)
        self.assertIn("--expected-version", step)
        self.assertIn("exit 75", step)

    def test_invalid_staged_rearm_is_exact_cas_and_restores_the_regression_trigger(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_rearm_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "STAGED",
            "version": 4,
            "approval": "protected-approval",
            "current_snapshot_hash": "c" * 64,
            "private_evidence_prefix": "https://github.com/o/r/actions/runs/123/artifacts/1",
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "e" * 64,
            "build_artifact_id": "456",
            "build_artifact_digest": "sha256:" + "f" * 64,
            "api_deployment_id": "dpl_invalid_config",
            "api_deployment_url": "https://invalid-config.vercel.app",
            "api_role": "SAFE_BASELINE",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 5,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        with tempfile.TemporaryDirectory(prefix="vowpic-staged-rearm-") as directory:
            evidence_path = Path(directory, "runtime-secret.json")
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": "vowpic.runtime-secret-control-proof.v1",
                        "passed": True,
                        "secret_name": "ACCEPTANCE_IDENTITY_HMAC_KEY",
                        "fingerprint_name": "ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
                        "minimum_length": 32,
                        "vercel_environment": "production",
                        "vercel_value_readable": False,
                        "vercel_secret_type": "sensitive",
                        "vercel_target": ["production"],
                        "project_id": "prj_project",
                        "team_id": "team_owner",
                        "source_sha": source_sha,
                        "runner_sha": runner_sha,
                        "workflow_run_id": "789",
                        "workflow_attempt": 1,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(register, "validate_staged_control_descendant") as control_diff,
                mock.patch.object(register, "create_engine", return_value=engine),
                mock.patch.object(register, "_configure_migration_timeouts") as configure_timeouts,
                mock.patch.object(register, "_advisory_lock"),
                mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
                mock.patch.object(register, "_read_activation", return_value=activation),
            ):
                result = register._rearm_invalid_staged(
                    "postgresql://migration",
                    source_sha=source_sha,
                    runner_sha=runner_sha,
                    workflow_run_id="789",
                    workflow_attempt=1,
                    expected_source_sha=source_sha,
                    expected_workflow_run_id="123",
                    expected_version=4,
                    approval="protected-approval",
                    evidence_prefix="https://github.com/o/r/actions/runs/789/artifacts/2",
                    runtime_secret_evidence=evidence_path,
                    project_id="prj_project",
                    team_id="team_owner",
                )

        control_diff.assert_called_once_with(source_sha, runner_sha)
        self.assertEqual(result["state"], "STAGED_REARMED")
        self.assertEqual(result["next_workflow_attempt"], 2)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        disable_index = next(
            index for index, statement in enumerate(statements)
            if "DISABLE TRIGGER trg_release_activation_regression" in statement
        )
        update_index = next(
            index for index, statement in enumerate(statements)
            if "UPDATE release_activations" in statement
        )
        enable_index = next(
            index for index, statement in enumerate(statements)
            if "ENABLE TRIGGER trg_release_activation_regression" in statement
        )
        self.assertLess(disable_index, update_index)
        self.assertLess(update_index, enable_index)
        update = statements[update_index]
        self.assertIn("phase = 'RESERVED'", update)
        self.assertIn("runtime_bundle_id = NULL", update)
        self.assertIn("manifest_sha256 = NULL", update)
        self.assertIn("api_deployment_id = NULL", update)
        self.assertIn("updated_at = CURRENT_TIMESTAMP", update)
        self.assertIn("version = :expected_version", update)
        configure_timeouts.assert_called_once_with(connection)
        self.assertRegex(result["previous_coordinates_sha256"], r"^[0-9a-f]{64}$")

    def test_staged_runtime_secret_rearm_rebuilds_only_the_immutable_source(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        self.assertIn("rearm_staged_after_config_repair", workflow)
        self.assertIn('"verify-runtime-secret"', register_source)
        self.assertIn('"rearm-staged"', register_source)
        secret_proof = workflow.index(
            "- name: Prove the repaired Production Sensitive secret control"
        )
        reservation_upload = workflow.index(
            "- name: Persist protected evidence before the first irreversible boundary"
        )
        rearm = workflow.index(
            "- name: Atomically rearm the invalid unpromoted STAGED binding"
        )
        adopt = workflow.index(
            "- name: Atomically adopt the immutable STAGED deployment"
        )
        self.assertLess(secret_proof, reservation_upload)
        self.assertLess(reservation_upload, rearm)
        self.assertLess(rearm, adopt)
        secret_proof_step = workflow[secret_proof:reservation_upload]
        self.assertIn("ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256 production", secret_proof_step)
        self.assertIn("--force --no-sensitive --yes", secret_proof_step)
        self.assertIn('os.environ["ACCEPTANCE_IDENTITY_HMAC_KEY"]', secret_proof_step)
        self.assertNotIn("echo $ACCEPTANCE_IDENTITY_HMAC_KEY", secret_proof_step)
        rearm_step = workflow[rearm:adopt]
        self.assertIn("--runtime-secret-evidence", rearm_step)
        self.assertIn("VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}", rearm_step)
        self.assertIn("VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}", rearm_step)
        self.assertIn("exit 75", rearm_step)
        adopt_condition = workflow[adopt : workflow.index("- name: Provision and publish", adopt)]
        self.assertIn("inputs.rearm_staged_after_config_repair != true", adopt_condition)

        build_start = workflow.index("- name: Build the predeploy output")
        build_end = workflow.index("- name: Encrypt the predeploy output", build_start)
        build_step = workflow[build_start:build_end]
        self.assertIn('git worktree add --detach "$RUNTIME_SOURCE_DIR" "$SOURCE_SHA"', build_step)
        self.assertIn('git -C "$RUNTIME_SOURCE_DIR" rev-parse HEAD', build_step)
        self.assertIn(
            '--directory "$RUNNER_TEMP" safe-baseline-deploy-root',
            build_step,
        )
        self.assertIn("--action materialize-deploy-root", build_step)
        self.assertIn('--source-output "$RUNTIME_OUTPUT"', build_step)
        self.assertIn('--destination-root "$DEPLOY_ROOT"', build_step)
        self.assertIn("--action verify-runtime-secret", build_step)
        self.assertIn("--runtime-secret-fingerprint ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256", build_step)
        self.assertNotIn('"$VERCEL_CLI" build --prod', build_step.split("cd \"$RUNTIME_SOURCE_DIR\"", 1)[0])
        deploy_start = workflow.index(
            "- name: Recover or create the one allowed staged deployment"
        )
        deploy_end = workflow.index(
            "- name: Resolve one immutable deployment coordinate set",
            deploy_start,
        )
        deploy_step = workflow[deploy_start:deploy_end]
        self.assertEqual(deploy_step.count('--env "SOURCE_SHA=$SOURCE_SHA"'), 1)
        self.assertEqual(
            deploy_step.count('--env "VERCEL_GIT_COMMIT_SHA=$SOURCE_SHA"'),
            1,
        )
        self.assertEqual(
            deploy_step.count(
                '--meta "vowpicRuntimeIdentityContract=vowpic-runtime-identity-v1"'
            ),
            1,
        )
        self.assertEqual(
            deploy_step.count(
                "--deployment-bypass-header-env VERCEL_AUTOMATION_BYPASS_HEADER"
            ),
            2,
        )
        self.assertIn(
            "VERCEL_AUTOMATION_BYPASS_HEADER: "
            "${{ secrets.VERCEL_AUTOMATION_BYPASS_HEADER }}",
            deploy_step,
        )

    def test_staged_verifier_takeover_diff_is_exactly_release_control_only(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_staged_control_diff_contract",
        )
        source_sha = "a" * 40
        runner_sha = "b" * 40
        database_control_paths = {
            "backend/tests/test_production_database_logins.py",
            "release/safe-baseline-contract.json",
            "scripts/release/bootstrap_production_database_roles.sql",
            "scripts/release/production_database_login_proof.py",
            "scripts/release/provision_production_database_logins.py",
        }
        runtime_audit_control_paths = {
            "backend/tests/test_runtime_ddl_audit_collector.py",
            "scripts/release/collect_runtime_ddl_audit.py",
        }
        self.assertTrue(
            database_control_paths | runtime_audit_control_paths
            <= register.STAGED_TAKEOVER_ALLOWED_CONTROL_PATHS
        )
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(
                {
                    *register.STAGED_TAKEOVER_REQUIRED_CONTROL_PATHS,
                    *database_control_paths,
                    *runtime_audit_control_paths,
                    "backend/tests/test_ci_release_contract.py",
                    "docs/ai-worklog.md",
                }
            )
        ) + "\n"
        successful_calls = [
            subprocess.CompletedProcess([], 0, stdout=runner_sha + "\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=allowed_diff, stderr=""),
        ]
        with mock.patch.object(register.subprocess, "run", side_effect=successful_calls):
            register.validate_staged_control_descendant(source_sha, runner_sha)

        unsafe_diff = allowed_diff + "M\tbackend/app/main.py\n"
        unsafe_calls = [
            subprocess.CompletedProcess([], 0, stdout=runner_sha + "\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=unsafe_diff, stderr=""),
        ]
        with (
            mock.patch.object(register.subprocess, "run", side_effect=unsafe_calls),
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "non-control path: backend/app/main.py",
            ),
        ):
            register.validate_staged_control_descendant(source_sha, runner_sha)

        missing_required_diff = "M\tscripts/release/verify_safe_baseline.py\n"
        missing_calls = [
            subprocess.CompletedProcess([], 0, stdout=runner_sha + "\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=missing_required_diff, stderr=""),
        ]
        with (
            mock.patch.object(register.subprocess, "run", side_effect=missing_calls),
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "missing required reviewed control changes",
            ),
        ):
            register.validate_staged_control_descendant(source_sha, runner_sha)

    def test_reserved_build_repair_diff_is_exactly_the_reviewed_forward_fix(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_build_repair_diff_contract",
        )
        previous_source_sha = "a" * 40
        source_sha = "b" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(register.RESERVED_BUILD_REPAIR_REQUIRED_PATHS)
        ) + "\nM\tbackend/tests/test_ci_release_contract.py\n"

        def calls_for(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=diff, stderr=""),
            ]

        with mock.patch.object(
            register.subprocess,
            "run",
            side_effect=calls_for(allowed_diff),
        ), mock.patch.object(register, "validate_reserved_build_dependency_repair"):
            register.validate_reserved_build_repair_descendant(
                previous_source_sha,
                source_sha,
            )

        for unsafe_diff, error in (
            (
                allowed_diff + "M\tbackend/app/main.py\n",
                "unauthorized path: backend/app/main.py",
            ),
            (
                allowed_diff.replace(
                    "M\tfrontend/package-lock.json\n",
                    "A\tfrontend/package-lock.json\n",
                ),
                "unauthorized path: frontend/package-lock.json",
            ),
            (
                allowed_diff.replace("M\tfrontend/package-lock.json\n", ""),
                "missing required reviewed changes",
            ),
        ):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    register.subprocess,
                    "run",
                    side_effect=calls_for(unsafe_diff),
                ),
                mock.patch.object(register, "validate_reserved_build_dependency_repair"),
                self.assertRaisesRegex(register.SafeBaselineRegistrationError, error),
            ):
                register.validate_reserved_build_repair_descendant(
                    previous_source_sha,
                    source_sha,
                )

    def test_reserved_build_dependency_repair_is_only_adm_zip_060(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_dependency_repair_contract",
        )
        previous_source_sha = "a" * 40
        source_sha = "b" * 40
        package = json.loads(_read("frontend/package.json"))
        lock = json.loads(_read("frontend/package-lock.json"))
        previous_package = json.loads(json.dumps(package))
        previous_package["overrides"].pop("adm-zip")
        previous_lock = json.loads(json.dumps(lock))
        previous_lock["packages"]["node_modules/adm-zip"] = {
            "version": "0.5.16",
            "resolved": "https://registry.npmmirror.com/adm-zip/-/adm-zip-0.5.16.tgz",
            "integrity": (
                "sha512-TGw5yVi4saajsSEgz25grObGHEUaDrniwvA2qwSC060KfqGPdglh"
                "vPMA2lPIoxs3PQIItj2iag35fONcQqgUaQ=="
            ),
            "license": "MIT",
            "engines": {"node": ">=12.0"},
        }

        def validate(source_package: dict, source_lock: dict) -> None:
            with mock.patch.object(
                register,
                "_read_git_json",
                side_effect=[
                    previous_package,
                    source_package,
                    previous_lock,
                    source_lock,
                ],
            ):
                register.validate_reserved_build_dependency_repair(
                    previous_source_sha,
                    source_sha,
                )

        validate(package, lock)

        unsafe_package = json.loads(json.dumps(package))
        unsafe_package["dependencies"]["vue"] = "3.4.22"
        with self.assertRaisesRegex(
            register.SafeBaselineRegistrationError,
            "package.json beyond adm-zip",
        ):
            validate(unsafe_package, lock)

        unsafe_lock = json.loads(json.dumps(lock))
        unsafe_lock["packages"]["node_modules/adm-zip"]["integrity"] = "sha512:unexpected"
        with self.assertRaisesRegex(
            register.SafeBaselineRegistrationError,
            "package-lock.json beyond adm-zip",
        ):
            validate(package, unsafe_lock)

    def test_reserved_deploy_root_repair_is_exactly_the_second_forward_fix(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_deploy_root_repair_contract",
        )
        previous_source_sha = register.RESERVED_DEPLOY_ROOT_REPAIR_PREVIOUS_SOURCE_SHA
        source_sha = "b" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(register.RESERVED_DEPLOY_ROOT_REPAIR_REQUIRED_PATHS)
        ) + "\nM\tdocs/ai-worklog.md\n"

        def calls_for(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=diff, stderr=""),
            ]

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=calls_for(allowed_diff),
            ),
            mock.patch.object(
                register,
                "validate_reserved_build_dependency_repair",
            ) as dependency_repair,
        ):
            register.validate_reserved_build_repair_descendant(
                previous_source_sha,
                source_sha,
            )
            dependency_repair.assert_not_called()

        for unsafe_diff, error in (
            (
                allowed_diff + "M\tfrontend/package.json\n",
                "unauthorized path: frontend/package.json",
            ),
            (
                allowed_diff.replace(
                    "M\tbackend/tests/test_ci_release_contract.py\n",
                    "",
                ),
                "missing required reviewed changes",
            ),
        ):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    register.subprocess,
                    "run",
                    side_effect=calls_for(unsafe_diff),
                ),
                self.assertRaisesRegex(register.SafeBaselineRegistrationError, error),
            ):
                register.validate_reserved_build_repair_descendant(
                    previous_source_sha,
                    source_sha,
                )

    def test_reserved_vercel_python_repair_is_exactly_the_third_forward_fix(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_vercel_python_repair_contract",
        )
        previous_source_sha = (
            register.RESERVED_VERCEL_PYTHON_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "c" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(register.RESERVED_VERCEL_PYTHON_REPAIR_REQUIRED_PATHS)
        ) + "\nM\tdocs/ai-worklog.md\n"

        def calls_for(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=diff, stderr=""),
            ]

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=calls_for(allowed_diff),
            ),
            mock.patch.object(
                register,
                "validate_reserved_build_dependency_repair",
            ) as dependency_repair,
        ):
            register.validate_reserved_build_repair_descendant(
                previous_source_sha,
                source_sha,
            )
            dependency_repair.assert_not_called()

        for unsafe_diff, error in (
            (
                allowed_diff + "M\t.github/workflows/safe-baseline-release.yml\n",
                "unauthorized path: .github/workflows/safe-baseline-release.yml",
            ),
            (
                allowed_diff.replace(
                    "M\tbackend/tests/test_ci_release_contract.py\n",
                    "",
                ),
                "missing required reviewed changes",
            ),
        ):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    register.subprocess,
                    "run",
                    side_effect=calls_for(unsafe_diff),
                ),
                self.assertRaisesRegex(register.SafeBaselineRegistrationError, error),
            ):
                register.validate_reserved_build_repair_descendant(
                    previous_source_sha,
                    source_sha,
                )

    def test_reserved_vercel_python_pycache_repair_is_exactly_forward_only(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_vercel_python_pycache_repair_contract",
        )
        previous_source_sha = (
            register.RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "d" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(
                register.RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_REQUIRED_PATHS
            )
        ) + "\nM\tdocs/ai-worklog.md\n"

        def calls_for(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=diff, stderr=""),
            ]

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=calls_for(allowed_diff),
            ),
            mock.patch.object(
                register,
                "validate_reserved_build_dependency_repair",
            ) as dependency_repair,
        ):
            register.validate_reserved_build_repair_descendant(
                previous_source_sha,
                source_sha,
            )
            dependency_repair.assert_not_called()

        for unsafe_diff, error in (
            (
                allowed_diff + "M\tfrontend/package-lock.json\n",
                "unauthorized path: frontend/package-lock.json",
            ),
            (
                allowed_diff.replace(
                    "M\tscripts/release/register_safe_baseline.py\n",
                    "",
                ),
                "missing required reviewed changes",
            ),
        ):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    register.subprocess,
                    "run",
                    side_effect=calls_for(unsafe_diff),
                ),
                self.assertRaisesRegex(register.SafeBaselineRegistrationError, error),
            ):
                register.validate_reserved_build_repair_descendant(
                    previous_source_sha,
                    source_sha,
                )

    def test_reserved_tracked_env_example_repair_is_exactly_forward_only(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_tracked_env_example_repair_contract",
        )
        previous_source_sha = (
            register.RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_PREVIOUS_SOURCE_SHA
        )
        source_sha = "e" * 40
        allowed_diff = "\n".join(
            f"M\t{path}"
            for path in sorted(
                register.RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_REQUIRED_PATHS
            )
        ) + "\nM\tdocs/ai-worklog.md\n"

        def calls_for(diff: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess([], 0, stdout=source_sha + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=diff, stderr=""),
            ]

        with (
            mock.patch.object(
                register.subprocess,
                "run",
                side_effect=calls_for(allowed_diff),
            ),
            mock.patch.object(
                register,
                "validate_reserved_build_dependency_repair",
            ) as dependency_repair,
        ):
            register.validate_reserved_build_repair_descendant(
                previous_source_sha,
                source_sha,
            )
            dependency_repair.assert_not_called()

        for unsafe_diff, error in (
            (
                allowed_diff + "M\t.env.example\n",
                "unauthorized path: .env.example",
            ),
            (
                allowed_diff.replace(
                    "M\tbackend/tests/test_ci_release_contract.py\n",
                    "",
                ),
                "missing required reviewed changes",
            ),
        ):
            with (
                self.subTest(error=error),
                mock.patch.object(
                    register.subprocess,
                    "run",
                    side_effect=calls_for(unsafe_diff),
                ),
                self.assertRaisesRegex(register.SafeBaselineRegistrationError, error),
            ):
                register.validate_reserved_build_repair_descendant(
                    previous_source_sha,
                    source_sha,
                )

    def test_reserved_retry_rejects_a_decreasing_workflow_attempt(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_reserved_retry_contract",
        )
        source_sha = "a" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "run-1",
            "workflow_attempt": 2,
            "phase": "RESERVED",
        }
        connection = mock.MagicMock()

        class _Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return False

        class _Engine:
            def begin(self):
                return _Transaction()

            def dispose(self):
                return None

        with (
            mock.patch.object(register, "_evidence_pair_sha256", return_value="b" * 64),
            mock.patch.object(register, "_read_only_database_identity", return_value=("system", "db", "1")),
            mock.patch.object(register, "create_engine", return_value=_Engine()),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_database_identity", return_value=("system", "db", "1")),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            with self.assertRaises(ValueError):
                register._reserve(
                    "postgresql://migration",
                    inventory_database_url="postgresql://inventory",
                    source_sha=source_sha,
                    workflow_run_id="run-1",
                    workflow_attempt=1,
                    approval="approval",
                    inventory_report=Path("inventory.json"),
                    restore_report=Path("restore.json"),
                    evidence_prefix="security-baseline/run-1",
                    inject_failure=None,
                )

    def test_reservation_configures_bounded_migration_timeouts_before_lock_and_upgrade(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_migration_timeout_contract",
        )
        events: list[str] = []
        connection = mock.MagicMock()
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection

        with (
            mock.patch.object(register, "_evidence_pair_sha256", return_value="b" * 64),
            mock.patch.object(register, "_read_only_database_identity", return_value=("system", "db", "1")),
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(
                register,
                "_configure_migration_timeouts",
                side_effect=lambda _connection: events.append("timeouts"),
            ),
            mock.patch.object(
                register,
                "_advisory_lock",
                side_effect=lambda _connection: events.append("lock"),
            ),
            mock.patch.object(register, "_database_identity", return_value=("system", "db", "1")),
            mock.patch.object(register, "_current_revision", side_effect=[register.OLD_SCHEMA, register.TARGET_SCHEMA]),
            mock.patch.object(register, "_read_activation", return_value=None),
            mock.patch.object(
                register,
                "_alembic_upgrade_on_connection",
                side_effect=lambda _connection: events.append("upgrade"),
            ),
            mock.patch.object(
                register,
                "reconcile_inventory_rls_policies",
                side_effect=lambda _connection: events.append("inventory-rls") or {
                    "policy_contract_complete": True
                },
            ),
        ):
            result = register._reserve(
                "postgresql://migration",
                inventory_database_url="postgresql://inventory",
                source_sha="a" * 40,
                workflow_run_id="run-1",
                workflow_attempt=1,
                approval="approval",
                inventory_report=Path("inventory.json"),
                restore_report=Path("restore.json"),
                evidence_prefix="security-baseline/run-1",
                inject_failure=None,
            )

        self.assertEqual(events, ["timeouts", "lock", "upgrade", "inventory-rls"])
        self.assertEqual(result["state"], "RESERVED")

    def test_migration_timeouts_are_finite_transaction_local_settings(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_migration_timeout_values_contract",
        )
        connection = mock.MagicMock()

        register._configure_migration_timeouts(connection)

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertEqual(
            statements,
            [
                "SET LOCAL lock_timeout = '15s'",
                "SET LOCAL statement_timeout = '5min'",
            ],
        )

    def test_read_only_preflight_rejects_a_decreasing_attempt_before_external_effects(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_preflight_attempt_contract",
        )
        self.assertIn("workflow_attempt", inspect.signature(register._preflight).parameters)
        source_sha = "a" * 40
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "run-1",
            "workflow_attempt": 2,
            "phase": "RESERVED",
        }
        connection = mock.MagicMock()
        connection.execute.return_value.scalar_one.return_value = "on"
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = connection

        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            with self.assertRaises(ValueError):
                register._preflight(
                    "postgresql://read-only",
                    source_sha=source_sha,
                    workflow_run_id="run-1",
                    workflow_attempt=1,
                )

    def test_reservation_rejects_unproven_inventory_or_restore_evidence(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_evidence_contract",
        )
        read_only = {
            "authenticated_role_name": "vowpic_inventory_login",
            "role_name": "vowpic_inventory_login",
            "transaction_read_only": True,
            "default_transaction_read_only": True,
            "role_superuser": False,
            "role_create_db": False,
            "role_create_role": False,
            "role_replication": False,
            "role_bypass_rls": False,
            "role_membership_count": 0,
            "owned_object_count": 0,
            "inventory_table_count": 1,
            "readable_inventory_table_count": 1,
            "inventory_sequence_count": 0,
            "readable_inventory_sequence_count": 0,
            "rls_table_count": 0,
            "inventory_select_policy_count": 0,
            "invalid_inventory_policy_count": 0,
            "writable_table_count": 0,
            "write_probe_sqlstate": "25006",
        }
        inventory = {
            "schema_revision": register.OLD_SCHEMA,
            "users": {"total": 1},
            "ledger": {},
            "orders": {},
            "objects": {},
            "conflict_group_hmacs": {},
            "read_only_proof": read_only,
            "url_inventory_hmac_sha256": "a" * 64,
        }
        restore = {
            "passed": True,
            "archive_sha256": "b" * 64,
            "source_read_only": read_only,
            "comparison": {
                "matches": True,
                "schema_revision": register.OLD_SCHEMA,
                "table_count": 1,
                "row_counts": {"users": 1},
                "row_counts_sha256": "c" * 64,
                "url_inventory_sha256": "d" * 64,
                "fk_orphans": 0,
                "ledger_mismatch_users": 0,
            },
            "cleanup": {"database_dropped": True, "role_dropped": True},
        }
        register.validate_reservation_evidence(inventory, restore)
        self.assertEqual(register.RESERVATION_TTL_MINUTES, 120)
        with self.assertRaises(ValueError):
            register.validate_reservation_evidence(inventory, dict(restore, passed=False))
        with self.assertRaises(ValueError):
            register.validate_reservation_evidence(
                dict(inventory, schema_revision=register.TARGET_SCHEMA),
                restore,
            )
        identity = ("system-identifier", "production", "database-oid")
        register.validate_database_identity(identity, identity)
        with self.assertRaises(ValueError):
            register.validate_database_identity(
                identity,
                ("different-system", "production", "database-oid"),
            )

    def test_crash_recovery_is_unambiguous_and_never_rebuilds_after_staged(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_recovery_contract",
        )
        self.assertEqual(register.recovery_decision("RESERVED", candidate_count=0), "DEPLOY_ONCE")
        self.assertEqual(register.recovery_decision("RESERVED", candidate_count=1), "BIND_ONLY_CANDIDATE")
        self.assertEqual(
            register.recovery_decision("RESERVED", candidate_count=2),
            "MANUAL_FORWARD_DISPOSITION",
        )
        self.assertEqual(
            register.recovery_decision("STAGED", candidate_count=0),
            "REUSE_RECORDED_DEPLOYMENT",
        )
        self.assertEqual(
            register.recovery_decision("PROMOTED", candidate_count=0, formal_domain_matches=True),
            "ADVANCE_FORMAL_VERIFICATION",
        )
        self.assertEqual(
            register.recovery_decision("PROMOTED", candidate_count=0, formal_domain_matches=False),
            "KEEP_EDGE_DENY",
        )
        self.assertEqual(
            register.promotion_recovery_decision(
                "PROMOTION_ARMED",
                formal_deployment_id="dpl_target",
                target_deployment_id="dpl_target",
            ),
            "ADVANCE_PROMOTED_WITHOUT_PROMOTE",
        )
        self.assertEqual(
            register.promotion_recovery_decision(
                "PROMOTION_ARMED",
                formal_deployment_id="dpl_old",
                target_deployment_id="dpl_target",
            ),
            "PROMOTE_ONCE",
        )
        self.assertEqual(
            register.promotion_recovery_decision(
                "PROMOTED",
                formal_deployment_id="dpl_old",
                target_deployment_id="dpl_target",
            ),
            "KEEP_EDGE_DENY",
        )

    def test_recovery_binds_attempt_and_scans_every_deployment_page_for_exact_manifest(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_paginated_recovery_contract",
        )
        deployment_signature = inspect.signature(register._recover_deployment)
        promotion_signature = inspect.signature(register._recover_promotion)
        self.assertIn("workflow_attempt", deployment_signature.parameters)
        self.assertIn("workflow_attempt", promotion_signature.parameters)
        self.assertIs(
            deployment_signature.parameters["manifest_sha256"].default,
            inspect.Parameter.empty,
        )

        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        manifest_sha256 = "c" * 64

        def deployment(deployment_id: str, manifest: str) -> dict[str, object]:
            return {
                "uid": deployment_id,
                "url": f"{deployment_id}.vercel.app",
                "state": "READY",
                "meta": {
                    "vowpicSourceSha": source_sha,
                    "vowpicRuntimeBundleId": runtime_bundle_id,
                    "vowpicBuildSha256": manifest,
                    "vowpicReleaseRole": "SAFE_BASELINE",
                    "vowpicRuntimeIdentityContract": register.RUNTIME_IDENTITY_CONTRACT,
                },
            }

        first = mock.MagicMock()
        first.json.return_value = {
            "deployments": [deployment("dpl_wrong", "d" * 64)],
            "pagination": {"next": 1700000000000},
        }
        second = mock.MagicMock()
        second.json.return_value = {
            "deployments": [deployment("dpl_exact", manifest_sha256)],
            "pagination": {"next": None},
        }
        runtime = mock.MagicMock(status_code=200)
        runtime.json.return_value = {
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": "dpl_exact",
        }
        with (
            mock.patch.object(
                register,
                "_preflight",
                return_value={"state": "RETRY_RESERVED"},
            ) as preflight,
            mock.patch.object(
                register.httpx,
                "get",
                side_effect=[first, second, runtime],
            ) as get,
        ):
            recovered = register._recover_deployment(
                "postgresql://read-only",
                source_sha=source_sha,
                workflow_run_id="12345",
                workflow_attempt=2,
                runtime_bundle_id=runtime_bundle_id,
                manifest_sha256=manifest_sha256,
                token="token",
                project_id="prj_vowpic",
                team_id="team_vowpic",
                bypass_secret="z" * 32,
            )

        self.assertEqual(recovered["candidate_count"], 1)
        self.assertEqual(recovered["candidate"]["deployment_id"], "dpl_exact")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["until"], "1700000000000")
        self.assertEqual(
            get.call_args_list[2].args[0],
            "https://dpl_exact.vercel.app/version",
        )
        self.assertEqual(
            get.call_args_list[2].kwargs["headers"][
                "x-vercel-protection-bypass"
            ],
            "z" * 32,
        )
        preflight.assert_called_once_with(
            "postgresql://read-only",
            source_sha=source_sha,
            workflow_run_id="12345",
            workflow_attempt=2,
        )

    def test_recovery_rejects_missing_manifest_and_repeated_pagination_cursor(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_recovery_failure_contract",
        )
        common = {
            "source_sha": "a" * 40,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "token": "token",
            "project_id": "prj_vowpic",
            "team_id": "",
            "bypass_secret": "z" * 32,
        }
        with mock.patch.object(register.httpx, "get") as get:
            with self.assertRaises(ValueError):
                register._recover_deployment(
                    "postgresql://read-only",
                    manifest_sha256="",
                    **common,
                )
            get.assert_not_called()

        page = mock.MagicMock()
        page.json.return_value = {"deployments": [], "pagination": {"next": 7}}
        with (
            mock.patch.object(
                register,
                "_preflight",
                return_value={"state": "RETRY_RESERVED"},
            ),
            mock.patch.object(register.httpx, "get", side_effect=[page, page]),
        ):
            with self.assertRaises(register.SafeBaselineRegistrationError):
                register._recover_deployment(
                    "postgresql://read-only",
                    manifest_sha256="c" * 64,
                    **common,
                )

    def test_recovery_never_redeploys_over_an_exact_non_ready_deployment(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_non_ready_recovery_contract",
        )
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        manifest_sha256 = "c" * 64
        page = mock.MagicMock()
        page.json.return_value = {
            "deployments": [
                {
                    "uid": "dpl_building",
                    "url": "dpl_building.vercel.app",
                    "state": "BUILDING",
                    "meta": {
                        "vowpicSourceSha": source_sha,
                        "vowpicRuntimeBundleId": runtime_bundle_id,
                        "vowpicBuildSha256": manifest_sha256,
                        "vowpicReleaseRole": "SAFE_BASELINE",
                        "vowpicRuntimeIdentityContract": register.RUNTIME_IDENTITY_CONTRACT,
                    },
                }
            ],
            "pagination": {"next": None},
        }
        with (
            mock.patch.object(
                register,
                "_preflight",
                return_value={"state": "RETRY_RESERVED"},
            ),
            mock.patch.object(register.httpx, "get", return_value=page),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "not READY",
            ):
                register._recover_deployment(
                    "postgresql://read-only",
                    source_sha=source_sha,
                    workflow_run_id="12345",
                    workflow_attempt=2,
                    runtime_bundle_id=runtime_bundle_id,
                    manifest_sha256=manifest_sha256,
                    token="token",
                    project_id="prj_vowpic",
                    team_id="team_vowpic",
                    bypass_secret="z" * 32,
                )

    def test_recovery_rejects_metadata_only_runtime_mismatch_before_redeploy(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_runtime_attestation_recovery_contract",
        )
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        manifest_sha256 = "c" * 64
        page = mock.MagicMock()
        page.json.return_value = {
            "deployments": [
                {
                    "uid": "dpl_metadata_only",
                    "url": "dpl_metadata_only.vercel.app",
                    "state": "READY",
                    "meta": {
                        "vowpicSourceSha": source_sha,
                        "vowpicRuntimeBundleId": runtime_bundle_id,
                        "vowpicBuildSha256": manifest_sha256,
                        "vowpicReleaseRole": "SAFE_BASELINE",
                        "vowpicRuntimeIdentityContract": register.RUNTIME_IDENTITY_CONTRACT,
                    },
                }
            ],
            "pagination": {"next": None},
        }
        runtime = mock.MagicMock(status_code=200)
        runtime.json.return_value = {
            "source_sha": "",
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": "dpl_metadata_only",
        }
        with (
            mock.patch.object(
                register,
                "_preflight",
                return_value={"state": "RETRY_RESERVED"},
            ),
            mock.patch.object(
                register.httpx,
                "get",
                side_effect=[page, runtime],
            ),
        ):
            recovered = register._recover_deployment(
                "postgresql://read-only",
                source_sha=source_sha,
                workflow_run_id="12345",
                workflow_attempt=2,
                runtime_bundle_id=runtime_bundle_id,
                manifest_sha256=manifest_sha256,
                token="token",
                project_id="prj_vowpic",
                team_id="team_vowpic",
                bypass_secret="z" * 32,
            )

        self.assertEqual(recovered["decision"], "DEPLOY_ONCE")
        self.assertEqual(recovered["candidate_count"], 0)
        self.assertEqual(recovered["runtime_mismatch_count"], 1)

    def test_recovery_ignores_a_legacy_deployment_without_runtime_identity_contract(
        self,
    ) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_legacy_runtime_identity_contract",
        )
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        manifest_sha256 = "c" * 64
        page = mock.MagicMock()
        page.json.return_value = {
            "deployments": [
                {
                    "uid": "dpl_legacy",
                    "url": "dpl_legacy.vercel.app",
                    "state": "READY",
                    "meta": {
                        "vowpicSourceSha": source_sha,
                        "vowpicRuntimeBundleId": runtime_bundle_id,
                        "vowpicBuildSha256": manifest_sha256,
                        "vowpicReleaseRole": "SAFE_BASELINE",
                    },
                }
            ],
            "pagination": {"next": None},
        }
        with (
            mock.patch.object(
                register,
                "_preflight",
                return_value={"state": "RETRY_RESERVED"},
            ),
            mock.patch.object(register.httpx, "get", return_value=page) as get,
        ):
            recovered = register._recover_deployment(
                "postgresql://read-only",
                source_sha=source_sha,
                workflow_run_id="12345",
                workflow_attempt=2,
                runtime_bundle_id=runtime_bundle_id,
                manifest_sha256=manifest_sha256,
                token="token",
                project_id="prj_vowpic",
                team_id="team_vowpic",
                bypass_secret="z" * 32,
            )

        self.assertEqual(recovered["decision"], "DEPLOY_ONCE")
        self.assertEqual(recovered["candidate_count"], 0)
        self.assertEqual(recovered["runtime_mismatch_count"], 0)
        get.assert_called_once()

    def test_runtime_attestation_retries_then_accepts_the_exact_identity(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_runtime_attestation_retry_contract",
        )
        request = httpx.Request(
            "GET",
            "https://dpl_exact.vercel.app/version",
        )
        unavailable = httpx.Response(503, request=request)
        exact = mock.MagicMock(status_code=200)
        exact.json.return_value = {
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "deployment_id": "dpl_exact",
        }
        with (
            mock.patch.object(
                register.httpx,
                "get",
                side_effect=[unavailable, exact],
            ) as get,
            mock.patch.object(register.time, "sleep") as sleep,
        ):
            matched = register._runtime_deployment_identity_matches(
                deployment_url="https://dpl_exact.vercel.app",
                deployment_id="dpl_exact",
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "b" * 64,
                bypass_secret="z" * 32,
            )

        self.assertTrue(matched)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(
            register.RUNTIME_ATTESTATION_RETRY_DELAYS[0]
        )

    def test_recovery_fails_closed_when_runtime_attestation_is_unreadable(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_unreadable_runtime_attestation_contract",
        )
        request = httpx.Request(
            "GET",
            "https://dpl_exact.vercel.app/version",
        )
        unavailable = httpx.Response(503, request=request)
        with (
            mock.patch.object(
                register.httpx,
                "get",
                return_value=unavailable,
            ) as get,
            mock.patch.object(register.time, "sleep") as sleep,
            self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                r"runtime attestation could not be read for dpl_exact "
                r"after 3 attempts \(HTTP 503\)",
            ),
        ):
            register._runtime_deployment_identity_matches(
                deployment_url="https://dpl_exact.vercel.app",
                deployment_id="dpl_exact",
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "b" * 64,
                bypass_secret="z" * 32,
            )
        self.assertEqual(get.call_count, register.RUNTIME_ATTESTATION_ATTEMPTS)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            list(register.RUNTIME_ATTESTATION_RETRY_DELAYS),
        )

    def test_reserved_build_is_durable_and_manifest_fenced_before_deploy(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        register_source = _read("scripts/release/register_safe_baseline.py")
        migration = _read("backend/alembic/versions/20260710_0013_ops_feature_flags.py")
        for action in (
            "preflight",
            "adopt-reserved",
            "rearm-reserved-build",
            "materialize-deploy-root",
            "bind-build",
            "recover-deployment",
            "recover-promotion",
        ):
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', register_source)
        self.assertGreaterEqual(register_source.count("not token"), 2)
        self.assertGreaterEqual(register_source.count("not project_id"), 2)
        self.assertGreaterEqual(register_source.count("not team_id"), 2)
        self.assertIn("or not bypass_header", register_source)
        self.assertIn("manifest is immutable once assigned", migration)
        self.assertRegex(
            workflow,
            r"uses:\s*actions/download-artifact@[0-9a-f]{40}",
        )
        self.assertIn("activation_manifest_sha256", workflow)
        self.assertIn("activation_workflow_attempt", workflow)
        self.assertIn("build_artifact_recovery_expired", workflow)
        self.assertIn("id: build_attempt", workflow)
        self.assertIn("id: build_lookup", workflow)
        self.assertIn("id: build_download", workflow)
        self.assertIn("github_artifact_evidence.py lookup", workflow)
        self.assertIn("steps.build_lookup.outputs.state == 'FOUND'", workflow)
        self.assertIn("steps.build_lookup.outputs.state == 'NOT_FOUND'", workflow)
        self.assertIn(
            "steps.preflight.outputs.build_artifact_recovery_expired == 'false'",
            workflow,
        )
        self.assertIn("unbound build recovery exceeded 90 days", workflow)
        self.assertIn(
            "steps.build_attempt.outputs.workflow_attempt",
            workflow,
        )
        self.assertIn('--build-artifact-attempt "$BUILD_ARTIFACT_ATTEMPT"', workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("tar --create", workflow)
        self.assertIn("tar --extract", workflow)
        self.assertIn("safe-baseline-build.tar", workflow)
        self.assertIn("safe-baseline-build.manifest.sha256", workflow)
        self.assertIn("SAFE_BASELINE_BUILD_ARTIFACT_KEY_B64", workflow)
        self.assertIn("build_artifact_crypto.py encrypt", workflow)
        self.assertIn("build_artifact_crypto.py decrypt", workflow)
        self.assertIn("safe-baseline-build.tar.enc", workflow)
        self.assertIn("safe-baseline-build.manifest.sha256.enc", workflow)
        self.assertIn('test "$MANIFEST_SHA256" = "$ARTIFACT_MANIFEST_SHA256"', workflow)
        self.assertIn("RETRY_PROMOTION_ARMED", workflow)
        build_step = workflow[
            workflow.index("- name: Build the predeploy output"):
            workflow.index("- name: Persist the exact predeploy output")
        ]
        self.assertIn("VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}", build_step)
        self.assertIn("VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}", build_step)
        self.assertIn('test -n "$VERCEL_TOKEN"', build_step)
        self.assertIn('test -n "$VERCEL_PROJECT_ID"', build_step)
        self.assertIn('test -n "$VERCEL_ORG_ID"', build_step)
        build = workflow.index('"$VERCEL_CLI" build')
        build_upload = workflow.index("id: build_evidence")
        bind_build = workflow.index("--action bind-build")
        recovery = workflow.index("--action recover-deployment")
        deploy = workflow.index('"$VERCEL_CLI" deploy')
        arm = workflow.index("--phase PROMOTION_ARMED")
        promote = workflow.index('"$VERCEL_CLI" promote')
        self.assertLess(build, build_upload)
        self.assertLess(build_upload, bind_build)
        self.assertLess(bind_build, recovery)
        self.assertLess(recovery, deploy)
        self.assertLess(arm, promote)
        self.assertIn(
            'if [[ "$PREFLIGHT_STATE" = "RETRY_PROMOTION_ARMED" ]]; then',
            workflow,
        )
        self.assertIn("steps.build_evidence.outputs.artifact-id", workflow)
        self.assertIn("steps.build_evidence.outputs.artifact-digest", workflow)
        self.assertIn('--build-artifact-id "$BUILD_ARTIFACT_ID"', workflow)
        self.assertIn('--build-artifact-digest "$BUILD_ARTIFACT_DIGEST"', workflow)
        self.assertIn("build_artifact_id", migration)
        self.assertIn("build_artifact_digest", migration)
        self.assertIn(".vercel/output", workflow)
        self.assertIn("deploy-root-materialization.json", workflow)
        self.assertIn('test ! -e "$DEPLOY_ROOT"', workflow)
        self.assertIn(
            '--directory "$RUNNER_TEMP" safe-baseline-deploy-root',
            workflow,
        )
        self.assertIn('cd "$DEPLOY_ROOT" && "$VERCEL_CLI" deploy --prebuilt', workflow)

        new_build_condition = workflow[
            workflow.index("- name: Build the predeploy output") :
            workflow.index("- name: Persist the exact predeploy output")
        ]
        self.assertNotIn("build_download.outcome != 'success'", new_build_condition)

        upload_step = workflow[
            workflow.index("- name: Persist the exact predeploy output") :
            workflow.index("- name: Prove the new predeploy output is durable")
        ]
        self.assertIn("safe-baseline-build.tar.enc", upload_step)
        self.assertIn("safe-baseline-build.manifest.sha256.enc", upload_step)
        self.assertNotRegex(
            upload_step,
            r"(?m)^\s*\$\{\{ runner\.temp \}\}/safe-baseline-build\.(?:tar|manifest\.sha256)\s*$",
        )

    def test_workflow_rechecks_current_main_at_each_irreversible_boundary(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        checks = [
            workflow.index(
                "- name: Recheck current main before migration, reservation, or staged verifier takeover"
            ),
            workflow.index("- name: Recheck current main before staged deployment"),
            workflow.index("- name: Recheck current main immediately before Promote"),
            workflow.index("- name: Recheck current main immediately after Promote"),
            workflow.index("- name: Recheck current main before FORMAL_VERIFIED"),
            workflow.index("- name: Recheck current main before COMPLETED"),
        ]
        reservation = workflow.index("--phase RESERVED")
        deploy = workflow.index('"$VERCEL_CLI" deploy')
        promote = workflow.index('"$VERCEL_CLI" promote')
        formal = workflow.index("--phase FORMAL_VERIFIED")
        completed = workflow.index("--phase COMPLETED")
        self.assertLess(checks[0], reservation)
        self.assertLess(checks[1], deploy)
        self.assertLess(checks[2], promote)
        self.assertGreater(checks[3], promote)
        self.assertLess(checks[4], formal)
        self.assertLess(checks[5], completed)
        for start in checks:
            snippet = workflow[start : start + 900]
            self.assertIn("verify_github_ref.py", snippet)
            self.assertIn("GITHUB_TOKEN: ${{ github.token }}", snippet)
            self.assertIn('--expected-sha "$RUNNER_SHA"', snippet)
        self.assertNotIn("git ls-remote", workflow)
        post_promote = workflow[checks[3] : workflow.index("- name: Reconfirm an already recorded promotion")]
        self.assertNotIn("if: ${{", post_promote)

    def test_authenticated_github_ref_guard_rejects_drift_and_api_failure(self) -> None:
        module = _load_script(
            "scripts/release/verify_github_ref.py",
            "verify_github_ref_contract",
        )
        expected = "a" * 40

        def matching(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("Authorization"), "Bearer github-token")
            self.assertEqual(
                request.url.path,
                "/repos/owner/repo/git/ref/heads/main",
            )
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": expected},
                },
            )

        with httpx.Client(transport=httpx.MockTransport(matching)) as client:
            result = module.verify_ref(
                repository="owner/repo",
                ref="refs/heads/main",
                expected_sha=expected,
                token="github-token",
                client=client,
            )
        self.assertEqual(result["sha"], expected)

        with httpx.Client(transport=httpx.MockTransport(matching)) as client:
            with self.assertRaises(module.GitHubRefVerificationError):
                module.verify_ref(
                    repository="owner/repo",
                    ref="refs/heads/main",
                    expected_sha="b" * 40,
                    token="github-token",
                    client=client,
                )
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, text="unavailable")
            )
        ) as client:
            with self.assertRaises(module.GitHubRefVerificationError):
                module.verify_ref(
                    repository="owner/repo",
                    ref="refs/heads/main",
                    expected_sha=expected,
                    token="github-token",
                    client=client,
                )

    def test_build_manifest_binds_directory_and_mode_metadata(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_manifest_metadata_contract",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output"
            output.mkdir()
            entry = output / "config.json"
            entry.write_text("{}", encoding="utf-8")

            content_only_hash = register._directory_sha256(output)
            (output / "empty-runtime-directory").mkdir()
            directory_hash = register._directory_sha256(output)
            self.assertNotEqual(content_only_hash, directory_hash)

            previous_mode = entry.stat().st_mode
            os.chmod(entry, previous_mode ^ 0o100)
            if entry.stat().st_mode != previous_mode:
                mode_hash = register._directory_sha256(output)
                self.assertNotEqual(directory_hash, mode_hash)

            sidecar = Path(temp_dir) / "manifest.sha256"
            sidecar.write_text(f"{'a' * 64}\n", encoding="ascii", newline="\n")
            self.assertEqual(register._read_manifest_sidecar(sidecar), "a" * 64)
            for invalid in (b"a" * 64, b"A" * 64 + b"\n", b"a" * 64 + b"\n\n"):
                sidecar.write_bytes(invalid)
                with self.assertRaises(ValueError):
                    register._read_manifest_sidecar(sidecar)

    def test_vercel_build_output_materialization_copies_regular_files(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_regular_materialized_output_contract",
        )
        with tempfile.TemporaryDirectory(prefix="vowpic-regular-output-") as directory:
            root = Path(directory)
            source_root = root / "source"
            source_output = source_root / ".vercel" / "output"
            destination = root / "artifact" / "deploy-root"
            source_output.mkdir(parents=True)
            (source_root / ".vercel" / "project.json").write_text(
                json.dumps(
                    {
                        "projectId": "prj_vowpic",
                        "orgId": "team_vowpic",
                        "projectName": "vowpic",
                        "settings": {},
                    }
                ),
                encoding="utf-8",
            )
            destination.parent.mkdir()
            (source_output / "config.json").write_text("{}", encoding="utf-8")
            referenced_asset = (
                source_root
                / "frontend"
                / "dist"
                / "build"
                / "h5"
                / "assets"
                / "admin.css"
            )
            referenced_asset.parent.mkdir(parents=True)
            referenced_asset.write_text(".admin { display: block; }\n", encoding="utf-8")
            generated_python_file = (
                source_root
                / ".vercel"
                / "python"
                / ".venv"
                / "lib"
                / "python3.12"
                / "site-packages"
                / "vowpic_runtime.py"
            )
            generated_python_file.parent.mkdir(parents=True)
            generated_python_file.write_text(
                "RUNTIME_READY = True\n",
                encoding="utf-8",
            )
            generated_python_bytecode = (
                source_root
                / ".vercel"
                / "python"
                / "pycache"
                / "home"
                / "runner"
                / "work"
                / "vowpic"
                / "api.cpython-312.pyc"
            )
            generated_python_bytecode.parent.mkdir(parents=True)
            generated_python_bytecode.write_bytes(b"VOWPIC-PYC")
            public_env_examples = {
                ".env.example": "APP_ENV=example\n",
                "backend/.env.example": "DATABASE_URL=postgresql://example\n",
                "frontend/.env.example": "VITE_API_BASE_URL=/api\n",
            }
            for relative, content in public_env_examples.items():
                example_file = source_root.joinpath(*PurePosixPath(relative).parts)
                example_file.parent.mkdir(parents=True, exist_ok=True)
                example_file.write_text(content, encoding="utf-8")
            function_config = source_output / "functions" / "api.func" / ".vc-config.json"
            function_config.parent.mkdir(parents=True)
            function_config.write_text(
                json.dumps(
                    {
                        "filePathMap": {
                            "assets/admin.css": (
                                "frontend/dist/build/h5/assets/admin.css"
                            ),
                            "site-packages/vowpic_runtime.py": (
                                ".vercel/python/.venv/lib/python3.12/"
                                "site-packages/vowpic_runtime.py"
                            ),
                            "_vc_pycache/api.cpython-312.pyc": (
                                ".vercel/python/pycache/home/runner/work/"
                                "vowpic/api.cpython-312.pyc"
                            ),
                            "examples/root.env": ".env.example",
                            "examples/backend.env": "backend/.env.example",
                            "examples/frontend.env": "frontend/.env.example",
                        }
                    }
                ),
                encoding="utf-8",
            )
            mismatched_destination = root / "artifact" / "mismatched-deploy-root"
            with self.assertRaisesRegex(ValueError, "unexpected shape"):
                register.materialize_vercel_deploy_root(
                    source_output,
                    source_root=source_root,
                    destination_root=mismatched_destination,
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_other",
                    expected_org_id="team_vowpic",
                )
            self.assertFalse(mismatched_destination.exists())
            report = register.materialize_vercel_deploy_root(
                source_output,
                source_root=source_root,
                destination_root=destination,
                source_sha="a" * 40,
                runner_sha="b" * 40,
                workflow_run_id="123",
                workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_org_id="team_vowpic",
            )
            self.assertEqual(
                (destination / ".vercel" / "output" / "config.json").read_text(
                    encoding="utf-8"
                ),
                "{}",
            )
            self.assertTrue((destination / ".vercel" / "project.json").is_file())
            self.assertEqual(report["materialized_symlinks"], 0)
            self.assertEqual(report["reference_declarations"], 6)
            self.assertEqual(report["referenced_files"], 6)
            self.assertEqual(
                (
                    destination
                    / "frontend"
                    / "dist"
                    / "build"
                    / "h5"
                    / "assets"
                    / "admin.css"
                ).read_bytes(),
                referenced_asset.read_bytes(),
            )
            self.assertEqual(
                (
                    destination
                    / ".vercel"
                    / "python"
                    / ".venv"
                    / "lib"
                    / "python3.12"
                    / "site-packages"
                    / "vowpic_runtime.py"
                ).read_bytes(),
                generated_python_file.read_bytes(),
            )
            self.assertEqual(
                (
                    destination
                    / ".vercel"
                    / "python"
                    / "pycache"
                    / "home"
                    / "runner"
                    / "work"
                    / "vowpic"
                    / "api.cpython-312.pyc"
                ).read_bytes(),
                generated_python_bytecode.read_bytes(),
            )
            for relative, content in public_env_examples.items():
                self.assertEqual(
                    destination.joinpath(*PurePosixPath(relative).parts).read_text(
                        encoding="utf-8"
                    ),
                    content,
                )
            self.assertEqual(report["manifest_sha256"], register._directory_sha256(destination))
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                register.materialize_vercel_deploy_root(
                    source_output,
                    source_root=source_root,
                    destination_root=destination,
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )

            missing_output = source_root / ".vercel" / "missing-output"
            missing_config = (
                missing_output / "functions" / "api.func" / ".vc-config.json"
            )
            missing_config.parent.mkdir(parents=True)
            (missing_output / "config.json").write_text("{}", encoding="utf-8")
            missing_config.write_text(
                json.dumps(
                    {
                        "filePathMap": {
                            "assets/missing.css": (
                                "frontend/dist/build/h5/assets/missing.css"
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            missing_destination = root / "artifact" / "missing-deploy-root"
            with self.assertRaisesRegex(ValueError, "missing reference"):
                register.materialize_vercel_deploy_root(
                    missing_output,
                    source_root=source_root,
                    destination_root=missing_destination,
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )
            self.assertFalse(missing_destination.exists())

    def test_vercel_build_output_materialization_is_self_contained_and_rejects_escape(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_materialized_output_contract",
        )
        with tempfile.TemporaryDirectory(prefix="vowpic-materialized-output-") as directory:
            root = Path(directory)
            source_root = root / "source"
            source_output = source_root / ".vercel" / "output"
            assets = source_root / "frontend" / "dist" / "build" / "h5" / "assets"
            destination_parent = root / "artifact"
            destination = destination_parent / "deploy-root"
            source_output.mkdir(parents=True)
            assets.mkdir(parents=True)
            destination_parent.mkdir()
            (source_output / "config.json").write_text("{}", encoding="utf-8")
            (source_root / ".vercel" / "project.json").write_text(
                json.dumps(
                    {
                        "projectId": "prj_vowpic",
                        "orgId": "team_vowpic",
                        "projectName": "vowpic",
                        "settings": {},
                    }
                ),
                encoding="utf-8",
            )
            asset = assets / "helper.js"
            asset.write_text("export const helper = true;\n", encoding="utf-8")
            static_assets = source_output / "static" / "assets"
            static_assets.mkdir(parents=True)
            link = static_assets / "helper.js"
            try:
                link.symlink_to(asset)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable on this platform: {exc}")
            function_config = source_output / "functions" / "api.func" / ".vc-config.json"
            function_config.parent.mkdir(parents=True)
            function_config.write_text(
                json.dumps(
                    {
                        "runtime": "nodejs24.x",
                        "filePathMap": {
                            "assets/helper.js": "frontend/dist/build/h5/assets/helper.js",
                            "assets/helper-copy.js": (
                                "frontend/dist/build/h5/assets/helper.js"
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = register.materialize_vercel_deploy_root(
                source_output,
                source_root=source_root,
                destination_root=destination,
                source_sha="a" * 40,
                runner_sha="b" * 40,
                workflow_run_id="123",
                workflow_attempt=2,
                expected_project_id="prj_vowpic",
                expected_org_id="team_vowpic",
            )
            materialized = (
                destination / ".vercel" / "output" / "static" / "assets" / "helper.js"
            )
            referenced = (
                destination / "frontend" / "dist" / "build" / "h5" / "assets" / "helper.js"
            )
            self.assertTrue(materialized.is_file())
            self.assertFalse(materialized.is_symlink())
            self.assertEqual(materialized.read_bytes(), asset.read_bytes())
            self.assertEqual(referenced.read_bytes(), asset.read_bytes())
            self.assertEqual(report["materialized_symlinks"], 1)
            self.assertEqual(report["reference_declarations"], 2)
            self.assertEqual(report["referenced_files"], 1)
            self.assertEqual(report["manifest_sha256"], register._directory_sha256(destination))
            asset.write_text("changed after materialization\n", encoding="utf-8")
            self.assertNotEqual(materialized.read_bytes(), asset.read_bytes())
            self.assertNotEqual(referenced.read_bytes(), asset.read_bytes())

            second_output = source_root / ".vercel" / "second-output"
            second_output.mkdir()
            external = root / "outside.txt"
            external.write_text("outside", encoding="utf-8")
            (second_output / "escape.txt").symlink_to(external)
            second_destination = destination_parent / "second-deploy-root"
            with self.assertRaisesRegex(ValueError, "escapes the immutable source root"):
                register.materialize_vercel_deploy_root(
                    second_output,
                    source_root=source_root,
                    destination_root=second_destination,
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )
            self.assertFalse(second_destination.exists())

            third_output = source_root / ".vercel" / "third-output"
            third_output.mkdir()
            sensitive = source_root / ".vercel" / ".env.production.local"
            sensitive.write_text("SECRET=not-for-output", encoding="utf-8")
            (third_output / "secret.txt").symlink_to(sensitive)
            with self.assertRaisesRegex(ValueError, "protected source metadata"):
                register.materialize_vercel_deploy_root(
                    third_output,
                    source_root=source_root,
                    destination_root=destination_parent / "third-deploy-root",
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )

            fourth_output = source_root / ".vercel" / "fourth-output"
            fourth_config = fourth_output / "functions" / "api.func" / ".vc-config.json"
            fourth_config.parent.mkdir(parents=True)
            (fourth_output / "config.json").write_text("{}", encoding="utf-8")
            fourth_config.write_text(
                json.dumps(
                    {
                        "filePathMap": {
                            "escape": "../outside.txt",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-canonical reference"):
                register.materialize_vercel_deploy_root(
                    fourth_output,
                    source_root=source_root,
                    destination_root=destination_parent / "fourth-deploy-root",
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )

            fifth_output = source_root / ".vercel" / "fifth-output"
            fifth_config = fifth_output / "functions" / "api.func" / ".vc-config.json"
            fifth_config.parent.mkdir(parents=True)
            (fifth_output / "config.json").write_text("{}", encoding="utf-8")
            fifth_config.write_text(
                json.dumps(
                    {
                        "filePathMap": {
                            "project-binding.json": ".vercel/project.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "protected source metadata"):
                register.materialize_vercel_deploy_root(
                    fifth_output,
                    source_root=source_root,
                    destination_root=destination_parent / "fifth-deploy-root",
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )

            unreviewed_env_example = source_root / "secrets" / ".env.example"
            unreviewed_env_example.parent.mkdir()
            unreviewed_env_example.write_text("SECRET=not-public\n", encoding="utf-8")
            sixth_output = source_root / ".vercel" / "sixth-output"
            sixth_config = sixth_output / "functions" / "api.func" / ".vc-config.json"
            sixth_config.parent.mkdir(parents=True)
            (sixth_output / "config.json").write_text("{}", encoding="utf-8")
            sixth_config.write_text(
                json.dumps(
                    {
                        "filePathMap": {
                            "unreviewed.env": "secrets/.env.example",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "protected source metadata"):
                register.materialize_vercel_deploy_root(
                    sixth_output,
                    source_root=source_root,
                    destination_root=destination_parent / "sixth-deploy-root",
                    source_sha="a" * 40,
                    runner_sha="b" * 40,
                    workflow_run_id="123",
                    workflow_attempt=2,
                    expected_project_id="prj_vowpic",
                    expected_org_id="team_vowpic",
                )

    def test_formal_verified_reference_cannot_be_silently_replaced_on_retry(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_formal_retry_contract",
        )
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": "a" * 40,
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "FORMAL_VERIFIED",
        }
        engine = mock.MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        args = SimpleNamespace(
            phase="FORMAL_VERIFIED",
            source_sha="a" * 40,
            workflow_run_id="12345",
            workflow_attempt=2,
        )
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            with self.assertRaises(register.SafeBaselineRegistrationError):
                register._advance_phase("postgresql://migration", args)

    def test_formal_verified_preflight_returns_its_immutable_evidence_coordinates(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_formal_preflight_contract",
        )
        artifact_helper = _load_script(
            "scripts/release/github_artifact_evidence.py",
            "github_artifact_formal_preflight_contract",
        )
        reference = artifact_helper.build_reference(
            repository="owner/repo",
            run_id="12345",
            artifact_id="67890",
            artifact_digest="sha256:" + "a" * 64,
            report_name="safe-baseline-formal.json",
        )
        report_sha256 = "b" * 64
        activation = {
            "source_sha": "c" * 40,
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "FORMAL_VERIFIED",
            "created_at": datetime.now(timezone.utc),
            "private_evidence_prefix": reference,
            "report_sha256": report_sha256,
        }
        engine = mock.MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            mock.MagicMock(),
            mock.MagicMock(scalar_one=mock.MagicMock(return_value="on")),
            mock.MagicMock(scalar_one=mock.MagicMock(return_value="on")),
        ]
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._preflight(
                "postgresql://read-only",
                source_sha="c" * 40,
                workflow_run_id="12345",
                workflow_attempt=2,
            )
        self.assertEqual(result["state"], "RETRY_FORMAL_VERIFIED")
        self.assertEqual(result["activation"]["private_evidence_prefix"], reference)
        self.assertEqual(result["activation"]["report_sha256"], report_sha256)

    def test_build_manifest_binding_is_idempotent_and_cas_fenced(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_build_binding_contract",
        )
        source_sha = "a" * 40
        manifest_sha256 = "b" * 64
        build_artifact_id = "98765"
        build_artifact_digest = "sha256:" + "c" * 64
        activation = {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_sha": source_sha,
            "workflow_run_id": "12345",
            "workflow_attempt": 1,
            "phase": "RESERVED",
            "version": 1,
            "manifest_sha256": None,
            "build_artifact_id": None,
            "build_artifact_digest": None,
        }
        connection = mock.MagicMock()
        connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": activation["id"],
            "version": 2,
        }
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            result = register._bind_build_manifest(
                "postgresql://migration",
                source_sha=source_sha,
                workflow_run_id="12345",
                workflow_attempt=2,
                build_artifact_attempt=1,
                build_artifact_id=build_artifact_id,
                build_artifact_digest=build_artifact_digest,
                manifest_sha256=manifest_sha256,
            )
        self.assertEqual(result["state"], "BUILD_BOUND")
        self.assertEqual(result["version"], 2)

        with mock.patch.object(register, "create_engine") as create_engine:
            with self.assertRaises(ValueError):
                register._bind_build_manifest(
                    "postgresql://migration",
                    source_sha=source_sha,
                    workflow_run_id="12345",
                    workflow_attempt=2,
                    build_artifact_attempt=1,
                    build_artifact_id=build_artifact_id,
                    build_artifact_digest=build_artifact_digest,
                    manifest_sha256="",
                )
            create_engine.assert_not_called()

        with (
            mock.patch.object(register, "create_engine", return_value=engine),
            mock.patch.object(register, "_advisory_lock"),
            mock.patch.object(register, "_current_revision", return_value=register.TARGET_SCHEMA),
            mock.patch.object(register, "_read_activation", return_value=activation),
        ):
            with self.assertRaises(register.SafeBaselineRegistrationError):
                register._bind_build_manifest(
                    "postgresql://migration",
                    source_sha=source_sha,
                    workflow_run_id="12345",
                    workflow_attempt=2,
                    build_artifact_attempt=2,
                    build_artifact_id=build_artifact_id,
                    build_artifact_digest=build_artifact_digest,
                    manifest_sha256=manifest_sha256,
                )

    def test_promotion_recovery_requires_ready_deployment_in_the_exact_project(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_promotion_project_contract",
        )
        preflight = {
            "state": "RETRY_PROMOTION_ARMED",
            "activation": {
                "api_deployment_id": "dpl_target",
                "api_deployment_url": "https://target.vercel.app",
            },
        }
        response = mock.MagicMock(status_code=200)
        response.json.return_value = {
            "uid": "dpl_target",
            "readyState": "READY",
        }
        common = {
            "source_sha": "a" * 40,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "token": "token",
            "project_id": "prj_vowpic",
            "team_id": "team_vowpic",
            "formal_domain": "www.vowpic.com",
        }
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", return_value=response),
        ):
            with self.assertRaises(register.SafeBaselineRegistrationError):
                register._recover_promotion("postgresql://read-only", **common)

        response.json.return_value = []
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", return_value=response),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "invalid JSON",
            ):
                register._recover_promotion("postgresql://read-only", **common)

        response.status_code = 404
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", return_value=response),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "cannot prove",
            ):
                register._recover_promotion("postgresql://read-only", **common)

        response.json.return_value = {
            "uid": "dpl_target",
            "readyState": "ERROR",
            "projectId": "prj_vowpic",
        }
        response.status_code = 200
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", return_value=response),
        ):
            with self.assertRaises(register.SafeBaselineRegistrationError):
                register._recover_promotion("postgresql://read-only", **common)

        with mock.patch.object(register, "_preflight", return_value=preflight):
            with self.assertRaises(ValueError):
                register._recover_promotion(
                    "postgresql://read-only",
                    **{**common, "formal_domain": "https://www.vowpic.com?unexpected=1"},
                )

    def test_promotion_recovery_blocks_existing_requests_and_rolling_releases(self) -> None:
        register = _load_script(
            "scripts/release/register_safe_baseline.py",
            "register_safe_baseline_promotion_request_contract",
        )
        preflight = {
            "state": "RETRY_PROMOTION_ARMED",
            "activation": {
                "api_deployment_id": "dpl_target",
                "api_deployment_url": "https://target.vercel.app",
            },
        }
        formal = mock.MagicMock(status_code=200)
        formal.json.return_value = {
            "uid": "dpl_old",
            "readyState": "READY",
            "projectId": "prj_vowpic",
        }
        project = mock.MagicMock(status_code=200)
        base_project = {
            "id": "prj_vowpic",
            "accountId": "team_vowpic",
        }
        common = {
            "source_sha": "a" * 40,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "token": "token",
            "project_id": "prj_vowpic",
            "team_id": "team_vowpic",
            "formal_domain": "www.vowpic.com",
        }

        project.json.return_value = {
            **base_project,
            "lastAliasRequest": {
                "type": "promote",
                "jobStatus": "in-progress",
                "toDeploymentId": "dpl_target",
                "requestedAt": 1,
            },
        }
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", side_effect=[formal, project]),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "already has a promotion request",
            ):
                register._recover_promotion("postgresql://read-only", **common)

        project.json.return_value = {**base_project, "rollingRelease": {"active": True}}
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", side_effect=[formal, project]),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "rolling release",
            ):
                register._recover_promotion("postgresql://read-only", **common)

        project.json.return_value = base_project
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", side_effect=[formal, project]),
        ):
            result = register._recover_promotion("postgresql://read-only", **common)
        self.assertEqual(result["decision"], "PROMOTE_ONCE")

        formal.json.return_value = {
            "uid": "dpl_target",
            "readyState": "READY",
            "projectId": "prj_vowpic",
        }
        project.json.return_value = {
            **base_project,
            "lastAliasRequest": {
                "type": "promote",
                "jobStatus": "succeeded",
                "toDeploymentId": "dpl_target",
                "requestedAt": 1,
            },
        }
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", side_effect=[formal, project]),
        ):
            result = register._recover_promotion("postgresql://read-only", **common)
        self.assertEqual(result["decision"], "ADVANCE_PROMOTED_WITHOUT_PROMOTE")

        project.json.return_value = base_project
        with (
            mock.patch.object(register, "_preflight", return_value=preflight),
            mock.patch.object(register.httpx, "get", side_effect=[formal, project]),
        ):
            with self.assertRaisesRegex(
                register.SafeBaselineRegistrationError,
                "promotion success proof is missing",
            ):
                register._recover_promotion("postgresql://read-only", **common)


class BaselineToolContractTest(unittest.TestCase):
    def test_liveness_is_process_only_and_version_exposes_public_runtime_coordinates(self) -> None:
        main_source = _read("backend/app/main.py")
        config_source = _read("backend/app/core/config.py")
        runtime_source = _read("backend/app/services/runtime_bundle_service.py")
        liveness_body = main_source[main_source.index('@app.get("/health")'):main_source.index('@app.get("/version")')]
        self.assertIn('"kind": "liveness"', liveness_body)
        self.assertNotIn("source_sha", liveness_body)
        self.assertIn("vercel_git_commit_sha", config_source)
        for coordinate in ("source_sha", "runtime_bundle_id", "deployment_id"):
            self.assertIn(f"{coordinate}:", runtime_source)

    def test_safe_baseline_verifier_reads_identity_from_version_not_liveness(self) -> None:
        verify = _load_script(
            "scripts/release/verify_safe_baseline.py",
            "verify_safe_baseline_runtime_identity_contract",
        )
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        deployment_id = "dpl_exact"
        health = mock.MagicMock(status_code=200)
        health.json.return_value = {
            "status": "healthy",
            "kind": "liveness",
            "readiness": "/health/ready",
        }
        version = mock.MagicMock(status_code=200)
        version.json.return_value = {
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": deployment_id,
        }
        client = mock.MagicMock()
        client.get.side_effect = [health, version]

        result = verify._verify_runtime_identity(
            client,
            {"User-Agent": "test"},
            expected_source_sha=source_sha,
            expected_runtime_bundle_id=runtime_bundle_id,
            expected_deployment_id=deployment_id,
        )

        self.assertEqual(result["source_sha"], source_sha)
        self.assertEqual([call.args[0] for call in client.get.call_args_list], ["/health", "/version"])

        health_with_state = mock.MagicMock(status_code=200)
        health_with_state.json.return_value = {**health.json.return_value, "source_sha": source_sha}
        client.get.side_effect = [health_with_state]
        with self.assertRaisesRegex(
            verify.SafeBaselineVerificationError,
            "liveness response contains non-process state",
        ):
            verify._verify_runtime_identity(
                client,
                {},
                expected_source_sha=source_sha,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_deployment_id=deployment_id,
            )

        wrong_version = mock.MagicMock(status_code=200)
        wrong_version.json.return_value = {**version.json.return_value, "source_sha": "c" * 40}
        client.get.side_effect = [health, wrong_version]
        with self.assertRaisesRegex(verify.SafeBaselineVerificationError, "runtime source_sha mismatch"):
            verify._verify_runtime_identity(
                client,
                {},
                expected_source_sha=source_sha,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_deployment_id=deployment_id,
            )

        missing_source = mock.MagicMock(status_code=200)
        missing_source.json.return_value = {
            **version.json.return_value,
            "source_sha": "",
        }
        client.get.side_effect = [health, missing_source]
        with self.assertRaisesRegex(
            verify.SafeBaselineVerificationError,
            "runtime source_sha is missing or invalid",
        ):
            verify._verify_runtime_identity(
                client,
                {},
                expected_source_sha=source_sha,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_deployment_id=deployment_id,
            )

        invalid_version = mock.MagicMock(status_code=200)
        invalid_version.json.side_effect = ValueError("invalid JSON")
        client.get.side_effect = [health, invalid_version]
        with self.assertRaisesRegex(
            verify.SafeBaselineVerificationError,
            "runtime version attestation returned an invalid payload",
        ):
            verify._verify_runtime_identity(
                client,
                {},
                expected_source_sha=source_sha,
                expected_runtime_bundle_id=runtime_bundle_id,
                expected_deployment_id=deployment_id,
            )

    def test_local_baseline_runs_the_real_frontend_unit_suite(self) -> None:
        script = _read("scripts/release/verify_baseline.ps1")
        self.assertIn("countTestCases", script)
        self.assertIn("TextTestRunner(stream=sys.stdout", script)
        self.assertEqual(script.count("npm run openapi:generate"), 2)
        self.assertIn("frontend generated API types are not byte-identical", script)
        self.assertIn("git diff --exit-code -- src/generated/api.d.ts", script)
        self.assertIn('frontend_api_types = "PASS"', script)
        self.assertIn("npm run typecheck", script)
        self.assertIn("npm run test:unit", script)
        self.assertIn("npm run build:web", script)
        self.assertIn('frontend_unit = "PASS"', script)
        self.assertNotIn("Task 22 has not installed Vitest", script)
        self.assertNotIn("TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN", script)

    def test_local_baseline_binds_the_actual_worktree_and_locked_environment(self) -> None:
        script = _read("scripts/release/verify_baseline.ps1")
        helper = _read("scripts/release/fingerprint_worktree.py")
        for field in (
            "base_sha",
            "source_sha",
            "code_identity",
            "working_tree_clean",
            "working_tree_sha256",
            "release_eligible",
        ):
            self.assertIn(field, script)
        self.assertIn("fingerprint_worktree.py", script)
        self.assertNotIn("git diff --binary", script)
        self.assertIn("UNCOMMITTED_WORKTREE", helper)
        self.assertIn("stdout=subprocess.PIPE", helper)
        self.assertIn("TASKS_1_4_BASELINE_PASS", script)
        self.assertNotIn("TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN", script)
        self.assertIn("python -m venv", script)
        self.assertIn("--require-hashes", script)
        self.assertIn("backend/requirements.lock.txt", script.replace("\\", "/"))
        self.assertIn("backend/requirements.windows.lock.txt", script.replace("\\", "/"))
        self.assertNotIn("source_sha = (git rev-parse HEAD).Trim()", script)
        self.assertNotIn("[System.IO.Path]::GetRelativePath", script)

    def test_local_baseline_marks_runtime_drift_not_release_eligible(self) -> None:
        script = _read("scripts/release/verify_baseline.ps1")
        for field in (
            "python_version",
            "node_version",
            "platform_system",
            "backend_lock_sha256",
            "runtime_alignment",
        ):
            self.assertIn(field, script)
        self.assertIn('"3.11.15"', script)
        self.assertIn('"24.17.0"', script)
        self.assertRegex(
            script,
            r"release_eligible\s*=\s*\[bool\]\$sourceIdentity\.release_eligible\s*-and\s*\$runtimeAlignment\s*-eq\s*\"PASS\"",
        )

    def test_worktree_fingerprint_is_independent_of_console_encoding(self) -> None:
        helper = ROOT / "scripts" / "release" / "fingerprint_worktree.py"
        self.assertTrue(helper.is_file(), "the byte-stable worktree fingerprint helper is missing")
        with tempfile.TemporaryDirectory(prefix="vowpic-fingerprint-encoding-") as directory:
            root = Path(directory)

            def git(*arguments: str) -> None:
                completed = subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            git("init")
            git("config", "user.email", "fingerprint@example.invalid")
            git("config", "user.name", "VowPic Fingerprint Test")
            tracked = root / "证据.txt"
            tracked.write_text("初始\n", encoding="utf-8")
            git("add", "--", tracked.name)
            git("commit", "-m", "baseline")
            tracked.write_text("修改\n", encoding="utf-8")
            (root / "新增.txt").write_text("内容\n", encoding="utf-8")

            payloads: list[dict[str, object]] = []
            for encoding in ("utf-8", "cp1252"):
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = encoding
                completed = subprocess.run(
                    [sys.executable, str(helper), "--root", str(root)],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payloads.append(json.loads(completed.stdout))
            self.assertEqual(payloads[0], payloads[1])
            self.assertEqual(payloads[0]["code_identity"], "UNCOMMITTED_WORKTREE")
            self.assertRegex(
                str(payloads[0]["working_tree_sha256"]), r"^[0-9a-f]{64}$"
            )

    def test_worktree_fingerprint_covers_clean_dirty_and_unicode_paths(self) -> None:
        helper = _load_script(
            "scripts/release/fingerprint_worktree.py",
            "fingerprint_worktree_contract",
        )
        with tempfile.TemporaryDirectory(prefix="vowpic-fingerprint-") as directory:
            root = Path(directory)

            def git(*arguments: str) -> None:
                completed = subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            git("init")
            git("config", "user.email", "fingerprint@example.invalid")
            git("config", "user.name", "VowPic Fingerprint Test")
            tracked = root / "证据.txt"
            tracked.write_text("初始\n", encoding="utf-8")
            git("add", "--", tracked.name)
            git("commit", "-m", "baseline")

            clean = helper.get_worktree_identity(root)
            self.assertEqual(clean["code_identity"], "CLEAN_COMMIT")
            self.assertEqual(clean["source_sha"], clean["base_sha"])
            self.assertIsNone(clean["working_tree_sha256"])
            self.assertTrue(clean["release_eligible"])

            tracked.write_text("修改\n", encoding="utf-8")
            (root / "新增.txt").write_text("内容\n", encoding="utf-8")
            dirty = helper.get_worktree_identity(root)
            self.assertEqual(dirty, helper.get_worktree_identity(root))
            self.assertEqual(dirty["code_identity"], "UNCOMMITTED_WORKTREE")
            self.assertIsNone(dirty["source_sha"])
            self.assertFalse(dirty["release_eligible"])
            self.assertRegex(str(dirty["working_tree_sha256"]), r"^[0-9a-f]{64}$")

    def test_safe_verifier_requires_app_layer_guards_and_no_side_effects(self) -> None:
        verify = _load_script(
            "scripts/release/verify_safe_baseline.py",
            "verify_safe_baseline_contract",
        )
        self.assertEqual(len(verify.GUARDED_ROUTE_PROBES), 17)
        self.assertEqual(len(verify.RETIRED_ROUTE_PROBES), 33)
        guarded = {probe.name: probe for probe in verify.GUARDED_ROUTE_PROBES}
        self.assertEqual(guarded["google_oauth_intent"].expected_status, 503)
        self.assertEqual(guarded["google_exchange"].expected_status, 503)
        self.assertEqual(
            guarded["google_exchange"].expected_code,
            "capability_disabled",
        )
        for name in (
            "private_media_upload",
            "gatekeeper",
            "order_create",
            "credit_checkout",
            "subscription_checkout",
            "subscription_cancel",
            "admin_creem_product_check",
            "admin_creem_checkout_probe",
            "admin_grant_credits",
            "admin_cleanup_paused",
        ):
            self.assertEqual(guarded[name].expected_status, 401, name)
            self.assertEqual(guarded[name].expected_code, "session_missing", name)
        for name in (
            "order_delete_paused",
            "credit_catalog_paused",
        ):
            self.assertEqual(guarded[name].expected_status, 503, name)
        for name in ("admin_generation_probe", "admin_regenerate", "ops_poll_pending_post"):
            self.assertEqual(guarded[name].expected_status, 410, name)
        retired = {probe.name: probe for probe in verify.RETIRED_ROUTE_PROBES}
        for name in (
            "manual_checkout_page_removed",
            "manual_checkout_submit_removed",
            "manual_checkout_complete_removed",
            "manual_checkout_fail_removed",
        ):
            self.assertEqual(retired[name].expected_status, 404, name)
        self.assertTrue(
            all(
                probe.expected_status == 410
                for name, probe in retired.items()
                if not name.startswith("manual_checkout_")
            )
        )
        before = {
            "table_counts": {"users": 1, "orders": 2},
            "public_reference_checksum": "a" * 64,
        }
        self.assertTrue(verify.compare_no_side_effect_snapshot(before, dict(before))["matches"])
        changed = {
            "table_counts": {"users": 1, "orders": 3},
            "public_reference_checksum": "a" * 64,
        }
        self.assertFalse(verify.compare_no_side_effect_snapshot(before, changed)["matches"])
        self.assertEqual(verify.INVALID_WEBHOOK_ALLOWED_STATUSES, {400, 401})
        self.assertNotIn(503, verify.LOGOUT_ALLOWED_STATUSES)
        source = _read("scripts/release/verify_safe_baseline.py")
        self.assertIn('"created_at": generated_at', source)
        self.assertNotIn("SAFE_BASELINE_PROBE_USER_BEARER", source)
        self.assertNotIn("--user-bearer-env", source)
        self.assertNotIn("--admin-token-env", source)
        self.assertIn('parser.add_argument("--request-origin", required=True)', source)
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        self.assertIn('--env "RELEASE_ROLE=SAFE_BASELINE"', workflow)
        self.assertNotIn("SAFE_BASELINE_PROBE_USER_BEARER", workflow)

    def test_safe_verifier_uses_the_formal_origin_for_staged_browser_probes(self) -> None:
        verify = _load_script(
            "scripts/release/verify_safe_baseline.py",
            "verify_safe_baseline_formal_origin_contract",
        )
        probe = next(
            item for item in verify.GUARDED_ROUTE_PROBES
            if item.name == "google_oauth_intent"
        )

        class ProbeClient:
            base_url = "https://temporary-deployment.vercel.app"

            def __init__(self) -> None:
                self.request_headers: dict[str, str] = {}

            def request(self, method, path, **kwargs):
                self.request_headers = dict(kwargs["headers"])
                return httpx.Response(
                    503,
                    json={"detail": {"code": "capability_disabled"}},
                )

        client = ProbeClient()
        result = verify._run_route_probe(
            client,
            {"x-vercel-protection-bypass": "redacted"},
            probe,
            cleanup_token="cleanup-token",
            request_origin="https://www.vowpic.com",
        )

        self.assertEqual(result["status"], 503)
        self.assertEqual(client.request_headers["Origin"], "https://www.vowpic.com")
        self.assertNotEqual(client.request_headers["Origin"], client.base_url)

    def test_external_runtime_and_edge_evidence_requires_authentication(self) -> None:
        verify = _load_script(
            "scripts/release/verify_safe_baseline.py",
            "verify_safe_baseline_authenticated_evidence_contract",
        )
        key = b"e" * 32
        payload = {
            "schema_version": "vowpic.runtime-ddl-audit.v1",
            "passed": True,
            "source_sha": "a" * 40,
            "statement_count": 8,
            "ddl_statement_count": 0,
        }
        payload["signature_hmac_sha256"] = verify.compute_evidence_hmac(payload, key)
        verify.verify_evidence_hmac(payload, key, label="runtime DDL audit")
        with self.assertRaises(verify.SafeBaselineVerificationError):
            verify.verify_evidence_hmac(
                dict(payload, statement_count=9),
                key,
                label="runtime DDL audit",
            )

    def test_signed_runtime_and_edge_evidence_is_fresh_and_attempt_bound(self) -> None:
        verify = _load_script(
            "scripts/release/verify_safe_baseline.py",
            "verify_safe_baseline_fresh_evidence_contract",
        )
        now = datetime.now(timezone.utc)
        key = b"f" * 32
        source_sha = "a" * 40
        runtime_bundle_id = "rtb_" + "b" * 64
        deployment_id = "dpl_exact"
        lockdown_after = "c" * 64
        lockdown_baseline = "d" * 64

        runtime_payload = {
            "schema_version": "vowpic.runtime-ddl-audit.v1",
            "passed": True,
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": deployment_id,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "statement_count": 8,
            "ddl_statement_count": 0,
            "coverage": sorted(verify.DDL_AUDIT_COVERAGE),
        }
        edge_payload = {
            "schema_version": "vowpic.edge-handoff.v1",
            "passed": True,
            "source_sha": source_sha,
            "runtime_bundle_id": runtime_bundle_id,
            "deployment_id": deployment_id,
            "workflow_run_id": "12345",
            "workflow_attempt": 2,
            "project_id": "prj_vowpic",
            "formal_domain": "www.vowpic.com",
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "lockdown_after_config_sha256": lockdown_after,
            "before_config_sha256": lockdown_after,
            "after_config_sha256": "d" * 64,
            "lockdown_baseline_config_sha256": lockdown_baseline,
            "runner_bypass_removed": True,
            "route_groups": {
                name: {
                    "rule_removed": True,
                    "read_back": True,
                    "no_side_effects": True,
                    "application_status": verify.EDGE_APPLICATION_GUARDS[name][0],
                    "application_code": verify.EDGE_APPLICATION_GUARDS[name][1],
                }
                for name in verify.EDGE_ROUTE_GROUPS
            },
        }

        def sign_and_write(directory: Path, name: str, payload: dict[str, object]) -> Path:
            signed = dict(payload)
            signed["signature_hmac_sha256"] = verify.compute_evidence_hmac(signed, key)
            path = directory / name
            path.write_text(json.dumps(signed), encoding="utf-8")
            return path

        with tempfile.TemporaryDirectory(prefix="vowpic-signed-evidence-") as directory_name:
            directory = Path(directory_name)
            runtime_path = sign_and_write(directory, "runtime.json", runtime_payload)
            edge_path = sign_and_write(directory, "edge.json", edge_payload)
            runtime_result = verify._validate_runtime_ddl_audit(
                runtime_path,
                source_sha=source_sha,
                runtime_bundle_id=runtime_bundle_id,
                deployment_id=deployment_id,
                workflow_run_id="12345",
                workflow_attempt=2,
                hmac_key=key,
                now=now,
            )
            self.assertEqual(runtime_result["statement_count"], 8)
            edge_result = verify._validate_edge_handoff(
                edge_path,
                source_sha=source_sha,
                runtime_bundle_id=runtime_bundle_id,
                deployment_id=deployment_id,
                workflow_run_id="12345",
                workflow_attempt=2,
                project_id="prj_vowpic",
                formal_domain="www.vowpic.com",
                expected_lockdown_after_config_sha256=lockdown_after,
                expected_lockdown_baseline_config_sha256=lockdown_baseline,
                hmac_key=key,
                now=now,
            )
            self.assertEqual(edge_result["project_id"], "prj_vowpic")

            invalid_payloads = (
                ("missing-run", {key: value for key, value in runtime_payload.items() if key != "workflow_run_id"}),
                ("zero-statements", {**runtime_payload, "statement_count": 0}),
                (
                    "stale",
                    {
                        **runtime_payload,
                        "generated_at": (now - timedelta(hours=2)).isoformat(),
                        "expires_at": (now - timedelta(hours=1)).isoformat(),
                    },
                ),
            )
            for label, invalid in invalid_payloads:
                with self.subTest(runtime=label):
                    path = sign_and_write(directory, f"{label}.json", invalid)
                    with self.assertRaises(verify.SafeBaselineVerificationError):
                        verify._validate_runtime_ddl_audit(
                            path,
                            source_sha=source_sha,
                            runtime_bundle_id=runtime_bundle_id,
                            deployment_id=deployment_id,
                            workflow_run_id="12345",
                            workflow_attempt=2,
                            hmac_key=key,
                            now=now,
                        )

            unlinked_edge = {**edge_payload, "before_config_sha256": "e" * 64}
            unlinked_path = sign_and_write(directory, "unlinked-edge.json", unlinked_edge)
            with self.assertRaises(verify.SafeBaselineVerificationError):
                verify._validate_edge_handoff(
                    unlinked_path,
                    source_sha=source_sha,
                    runtime_bundle_id=runtime_bundle_id,
                    deployment_id=deployment_id,
                    workflow_run_id="12345",
                    workflow_attempt=2,
                    project_id="prj_vowpic",
                    formal_domain="www.vowpic.com",
                    expected_lockdown_after_config_sha256=lockdown_after,
                    expected_lockdown_baseline_config_sha256=lockdown_baseline,
                    hmac_key=key,
                    now=now,
                )

    def test_workflow_binds_external_evidence_to_attempt_and_rechecks_formal_handoff(self) -> None:
        workflow = _read(".github/workflows/safe-baseline-release.yml")
        self.assertGreaterEqual(
            workflow.count('--expected-workflow-attempt "$GITHUB_RUN_ATTEMPT"'),
            3,
        )
        edge_step = workflow[
            workflow.index("- name: Install, read back, and verify the authenticated edge lockdown") :
            workflow.index("- name: Atomically migrate and reserve the install")
        ]
        self.assertIn("RETRY_FORMAL_VERIFIED", edge_step)
        self.assertIn("!=", edge_step)
        formal_step = workflow[
            workflow.index("- name: Hand off every edge group and verify the formal domain without a bypass") :
            workflow.index("- name: Complete the immutable one-time install")
        ]
        self.assertIn("--expected-project-id", formal_step)
        self.assertIn("--expected-formal-domain", formal_step)
        self.assertIn("--expected-lockdown-after-config-sha256", formal_step)
        self.assertIn("--expected-lockdown-baseline-config-sha256", formal_step)
        self.assertIn("manage_edge_lockdown.py handoff", formal_step)
        self.assertNotIn("EDGE_HANDOFF_REPORT_B64", workflow)
        self.assertNotIn("EDGE_LOCKDOWN_REPORT_B64", workflow)
        self.assertIn("github_artifact_evidence.py build-reference", formal_step)
        self.assertIn("steps.formal_evidence.outputs.artifact-id", formal_step)
        self.assertIn("steps.formal_evidence.outputs.artifact-digest", formal_step)
        self.assertIn('--evidence-prefix "$FORMAL_EVIDENCE_REFERENCE"', formal_step)
        self.assertIn("Verify stored FORMAL_VERIFIED evidence before completion", formal_step)
        self.assertIn("github_artifact_evidence.py verify-reference", formal_step)
        self.assertIn("activation_evidence_reference", workflow)
        self.assertIn("activation_report_sha256", workflow)
        record_step = workflow[
            workflow.index("- name: Record the verified formal handoff") :
            workflow.index("- name: Persist completion evidence")
        ]
        self.assertIn("state != 'RETRY_FORMAL_VERIFIED'", record_step)

    def test_risk_runbook_requires_external_auto_deploy_containment_evidence(self) -> None:
        runbook = _read("docs/operations/risk-lockdown-runbook.md")
        for required in (
            "disable Vercel Git Production auto-assignment",
            "disable deploy hooks",
            "Preview builds",
            "project-setting evidence",
            "last known deployment",
            "safe baseline",
            "NOT_RUN",
        ):
            self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
