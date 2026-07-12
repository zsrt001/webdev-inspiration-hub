"""Runtime configuration behavior tests."""

from pathlib import Path
import importlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402


class RuntimeConfigTest(unittest.TestCase):
    def test_sentry_uses_the_resolved_runtime_environment(self) -> None:
        source = (BACKEND_DIR / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("environment=settings.runtime_environment", source)
        self.assertNotIn('environment="production"', source)

    def test_vercel_preview_system_environment_selects_preview_runtime(self) -> None:
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.runtime_environment, "preview")

    def test_explicit_runtime_environment_overrides_vercel_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"RUNTIME_ENVIRONMENT": "production", "VERCEL_ENV": "preview"},
            clear=True,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.runtime_environment, "production")

    def test_development_cannot_enable_runtime_schema_writes(self) -> None:
        settings = Settings(debug=True, auto_create_tables=None)

        self.assertFalse(settings.should_auto_create_tables)

    def test_production_disables_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=False, auto_create_tables=None)

        self.assertFalse(settings.should_auto_create_tables)

    def test_legacy_auto_create_override_cannot_enable_runtime_schema_writes(self) -> None:
        settings = Settings(debug=False, auto_create_tables=True)

        self.assertFalse(settings.should_auto_create_tables)

    def test_vercel_uses_inline_generation_by_default(self) -> None:
        settings = Settings(vercel="1", task_execution_mode="auto")

        self.assertEqual(settings.generation_execution_mode, "inline")

    def test_allowed_image_models_cannot_be_widened_by_env(self) -> None:
        settings = Settings(
            _env_file=None,
            generation_allowed_image_models="gemini-3-pro-image-preview,gpt-image-2",
        )

        self.assertEqual(settings.generation_allowed_image_model_list, ["gemini-3-pro-image-preview"])
        self.assertFalse(settings.generation_image_model_allowed("gpt-image-2"))
        self.assertTrue(settings.generation_image_model_allowed("gemini-3-pro-image-preview"))

    def test_cleanup_cron_token_accepts_vercel_cron_secret_alias(self) -> None:
        settings = Settings(cleanup_cron_token="", cron_secret="vercel-cron-secret")

        self.assertEqual(settings.effective_cleanup_cron_token, "vercel-cron-secret")

    def test_vercel_s3_loopback_endpoint_can_fall_back_to_blob_when_token_exists(self) -> None:
        settings = Settings(
            vercel="1",
            storage_provider="s3",
            aws_s3_endpoint="http://127.0.0.1:9000",
            blob_read_write_token="vercel_blob_rw_token",
        )

        self.assertTrue(settings.aws_s3_endpoint_is_loopback)
        self.assertEqual(settings.effective_storage_provider, "vercel")

    def test_vercel_s3_loopback_endpoint_stays_s3_without_blob_token(self) -> None:
        settings = Settings(
            vercel="1",
            storage_provider="s3",
            aws_s3_endpoint="http://127.0.0.1:9000",
            blob_read_write_token="",
        )

        self.assertTrue(settings.aws_s3_endpoint_is_loopback)
        self.assertEqual(settings.effective_storage_provider, "s3")

    def test_evolink_generation_provider_config(self) -> None:
        settings = Settings(
            _env_file=None,
            generation_engine="evolink",
            evolink_poll_timeout=321,
            evolink_max_retries=4,
        )

        self.assertTrue(settings.using_evolink_generation)
        self.assertFalse(settings.using_wenwen_generation)
        self.assertEqual(settings.generation_provider_name, "evolink")
        self.assertEqual(settings.generation_poll_timeout, 321)
        self.assertEqual(settings.generation_max_retries, 4)

    def test_default_generation_provider_is_evolink(self) -> None:
        settings = Settings(_env_file=None)

        self.assertTrue(settings.using_evolink_generation)
        self.assertFalse(settings.using_wenwen_generation)
        self.assertEqual(settings.generation_provider_name, "evolink")

    def test_default_postprocess_variants_include_commercial_delivery_crops(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.postprocess_variants, "2x3,3x2,3x4,4x5,9x16,1x1")

    def test_control_plane_writer_uses_a_separate_explicit_database_url(self) -> None:
        settings = Settings(
            _env_file=None,
            runtime_environment="production",
            database_url="postgresql://app_runtime:runtime@db.internal:5432/vowpic",
            control_plane_database_url="postgresql://control_writer:writer@db.internal:5432/vowpic",
        )
        self.assertEqual(
            settings.effective_control_plane_database_url,
            "postgresql://control_writer:writer@db.internal:5432/vowpic",
        )

        missing = Settings(
            _env_file=None,
            runtime_environment="production",
            database_url="postgresql://app_runtime:runtime@db.internal:5432/vowpic",
            control_plane_database_url="",
        )
        self.assertEqual(missing.control_plane_database_config_errors, [
            "CONTROL_PLANE_DATABASE_URL is required outside development",
        ])

        same_login = Settings(
            _env_file=None,
            runtime_environment="production",
            database_url="postgresql://shared_login:runtime@db.internal:5432/vowpic",
            control_plane_database_url="postgresql://shared_login:writer@db.internal:5432/vowpic",
        )
        self.assertIn(
            "DATABASE_URL and CONTROL_PLANE_DATABASE_URL must use distinct login roles",
            same_login.control_plane_database_config_errors,
        )

        wrong_database = Settings(
            _env_file=None,
            runtime_environment="production",
            database_url="postgresql://app_runtime:runtime@db.internal:5432/vowpic",
            control_plane_database_url="postgresql://control_writer:writer@db.internal:5432/other",
        )
        self.assertIn(
            "DATABASE_URL and CONTROL_PLANE_DATABASE_URL must target the same database",
            wrong_database.control_plane_database_config_errors,
        )

        cross_project_pooler = Settings(
            _env_file=None,
            runtime_environment="production",
            database_url=(
                "postgresql://runtime.project_a:runtime@"
                "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
            ),
            control_plane_database_url=(
                "postgresql://writer.project_b:writer@"
                "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
            ),
        )
        self.assertIn(
            "DATABASE_URL and CONTROL_PLANE_DATABASE_URL must target the same database",
            cross_project_pooler.control_plane_database_config_errors,
        )

    def test_runtime_role_proof_rejects_owner_bypass_and_wrong_group(self) -> None:
        runtime_checks = importlib.import_module("app.core.runtime_checks")
        valid = {
            "current_user": "vowpic_app_runtime",
            "role_superuser": False,
            "role_bypass_rls": False,
            "control_table_owner": "postgres",
            "required_group_member": True,
            "forbidden_group_member": False,
        }
        self.assertEqual(
            runtime_checks.validate_database_role_proof(
                valid,
                required_group="vowpic_runtime",
                forbidden_group="vowpic_control_writer",
            ),
            "vowpic_app_runtime:vowpic_runtime",
        )
        for override in (
            {"current_user": "postgres", "control_table_owner": "postgres"},
            {"role_superuser": True},
            {"role_bypass_rls": True},
            {"required_group_member": False},
            {"forbidden_group_member": True},
        ):
            with self.subTest(override=override):
                with self.assertRaises(RuntimeError):
                    runtime_checks.validate_database_role_proof(
                        {**valid, **override},
                        required_group="vowpic_runtime",
                        forbidden_group="vowpic_control_writer",
                    )


class RuntimeFailClosedTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_hosted_config_makes_operational_readiness_fail_before_database(self) -> None:
        from app.core import runtime_checks

        database_check = AsyncMock()
        with (
            patch.object(
                runtime_checks,
                "validate_commercial_config_values",
                return_value=["preview runtime coordinates are missing"],
            ),
            patch.object(runtime_checks, "_check_database", database_check),
        ):
            report = await runtime_checks.run_readiness_checks(strict_mode=True)

        self.assertFalse(report["commercial_ready"])
        self.assertEqual(report["blockers"], ["commercial_config"])
        database_check.assert_not_awaited()

    async def test_invalid_hosted_config_makes_readiness_fail_before_database(self) -> None:
        from app.core import runtime_checks

        database_check = AsyncMock()
        with (
            patch.object(
                runtime_checks,
                "validate_commercial_config_values",
                return_value=["preview runtime coordinates are missing"],
            ),
            patch.object(runtime_checks, "_check_database", database_check),
        ):
            report = await runtime_checks.run_core_readiness_checks(strict_mode=True)

        self.assertFalse(report["ready"])
        self.assertEqual(report["blockers"], ["commercial_config"])
        self.assertFalse(report["checks"]["commercial_config"]["ok"])
        database_check.assert_not_awaited()

    async def test_invalid_preview_lifespan_starts_without_touching_readiness_dependencies(self) -> None:
        from app import main

        preview_settings = Settings(
            _env_file=None,
            debug=False,
            runtime_environment="preview",
            vercel="1",
        )
        readiness = AsyncMock()
        had_runtime_blocker = hasattr(main.app.state, "runtime_config_blocked")
        original_runtime_blocker = getattr(main.app.state, "runtime_config_blocked", False)
        try:
            with (
                patch.object(main, "settings", preview_settings),
                patch.object(
                    main,
                    "validate_commercial_config_values",
                    return_value=["preview runtime coordinates are missing"],
                ),
                patch.object(main, "run_core_readiness_checks", readiness),
            ):
                async with main.lifespan(main.app):
                    self.assertTrue(main.app.state.runtime_config_blocked)
        finally:
            if had_runtime_blocker:
                main.app.state.runtime_config_blocked = original_runtime_blocker
            elif hasattr(main.app.state, "runtime_config_blocked"):
                delattr(main.app.state, "runtime_config_blocked")

        readiness.assert_not_awaited()

    async def test_invalid_preview_blocks_api_before_database_but_keeps_liveness(self) -> None:
        from app import main
        from app.core.database import get_db

        database_opened = False

        async def unavailable_database():
            nonlocal database_opened
            database_opened = True
            raise RuntimeError("database dependency must not run")
            yield

        preview_settings = Settings(
            _env_file=None,
            debug=False,
            runtime_environment="preview",
            vercel="1",
        )
        original_overrides = dict(main.app.dependency_overrides)
        had_runtime_blocker = hasattr(main.app.state, "runtime_config_blocked")
        original_runtime_blocker = getattr(main.app.state, "runtime_config_blocked", False)
        main.app.dependency_overrides[get_db] = unavailable_database
        main.app.state.runtime_config_blocked = True
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        allowed_origin = main.cors_origins[0]
        try:
            with patch.object(main, "settings", preview_settings):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    health = await client.get("/health")
                    blocked = await client.get(
                        "/api/v1/ops/public_config",
                        headers={"Origin": allowed_origin},
                    )
                    preflight = await client.options(
                        "/api/v1/ops/public_config",
                        headers={
                            "Origin": allowed_origin,
                            "Access-Control-Request-Method": "GET",
                        },
                    )
        finally:
            main.app.dependency_overrides.clear()
            main.app.dependency_overrides.update(original_overrides)
            if had_runtime_blocker:
                main.app.state.runtime_config_blocked = original_runtime_blocker
            elif hasattr(main.app.state, "runtime_config_blocked"):
                delattr(main.app.state, "runtime_config_blocked")

        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(blocked.status_code, 503, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "runtime_not_ready")
        self.assertNotIn("preview runtime coordinates are missing", blocked.text)
        self.assertEqual(blocked.headers["access-control-allow-origin"], allowed_origin)
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertEqual(preflight.headers["access-control-allow-origin"], allowed_origin)
        self.assertFalse(database_opened)

    async def test_invalid_strict_default_environment_cannot_bypass_runtime_guard(self) -> None:
        from app import main
        from app.core.database import get_db

        database_opened = False

        async def unavailable_database():
            nonlocal database_opened
            database_opened = True
            raise RuntimeError("database dependency must not run")
            yield

        strict_default_settings = Settings(
            _env_file=None,
            debug=False,
            runtime_environment="development",
        )
        original_overrides = dict(main.app.dependency_overrides)
        had_runtime_blocker = hasattr(main.app.state, "runtime_config_blocked")
        original_runtime_blocker = getattr(main.app.state, "runtime_config_blocked", False)
        main.app.dependency_overrides[get_db] = unavailable_database
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        try:
            with (
                patch.object(main, "settings", strict_default_settings),
                patch.object(
                    main,
                    "validate_commercial_config_values",
                    return_value=["runtime environment is invalid"],
                ),
            ):
                if hasattr(main.app.state, "runtime_config_blocked"):
                    delattr(main.app.state, "runtime_config_blocked")
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    blocked = await client.get("/api/v1/ops/public_config")
        finally:
            main.app.dependency_overrides.clear()
            main.app.dependency_overrides.update(original_overrides)
            if had_runtime_blocker:
                main.app.state.runtime_config_blocked = original_runtime_blocker
            elif hasattr(main.app.state, "runtime_config_blocked"):
                delattr(main.app.state, "runtime_config_blocked")

        self.assertEqual(blocked.status_code, 503, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "runtime_not_ready")
        self.assertFalse(database_opened)

    async def test_non_debug_ops_readiness_cannot_disable_strict_mode(self) -> None:
        from fastapi import HTTPException
        from app.routers import ops

        readiness = AsyncMock(
            return_value={
                "commercial_ready": False,
                "blockers": ["commercial_config"],
                "checks": {},
            }
        )
        with (
            patch.object(
                ops,
                "settings",
                Settings(_env_file=None, debug=False, runtime_environment="preview"),
            ),
            patch.object(ops, "run_readiness_checks", readiness),
        ):
            with self.assertRaises(HTTPException) as raised:
                await ops.readiness(strict=False)

        self.assertEqual(raised.exception.status_code, 503)
        readiness.assert_awaited_once_with(
            probe_storage=False,
            probe_generation_queue=False,
            strict_mode=True,
        )

    async def test_missing_lifespan_state_blocks_even_valid_strict_config(self) -> None:
        from app import main
        from app.core.database import get_db

        database_opened = False

        async def unavailable_database():
            nonlocal database_opened
            database_opened = True
            raise RuntimeError("database dependency must not run")
            yield

        strict_settings = Settings(
            _env_file=None,
            debug=False,
            runtime_environment="production",
        )
        original_overrides = dict(main.app.dependency_overrides)
        had_runtime_blocker = hasattr(main.app.state, "runtime_config_blocked")
        original_runtime_blocker = getattr(main.app.state, "runtime_config_blocked", False)
        main.app.dependency_overrides[get_db] = unavailable_database
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        try:
            with (
                patch.object(main, "settings", strict_settings),
                patch.object(main, "validate_commercial_config_values", return_value=[]),
            ):
                if hasattr(main.app.state, "runtime_config_blocked"):
                    delattr(main.app.state, "runtime_config_blocked")
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    blocked = await client.get("/api/v1/ops/public_config")
        finally:
            main.app.dependency_overrides.clear()
            main.app.dependency_overrides.update(original_overrides)
            if had_runtime_blocker:
                main.app.state.runtime_config_blocked = original_runtime_blocker
            elif hasattr(main.app.state, "runtime_config_blocked"):
                delattr(main.app.state, "runtime_config_blocked")

        self.assertEqual(blocked.status_code, 503, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "runtime_not_ready")
        self.assertFalse(database_opened)


if __name__ == "__main__":
    unittest.main()
