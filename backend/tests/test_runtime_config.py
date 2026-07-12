"""Runtime configuration behavior tests."""

from pathlib import Path
import importlib
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402


class RuntimeConfigTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
