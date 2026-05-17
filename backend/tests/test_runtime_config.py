"""Runtime configuration behavior tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402


class RuntimeConfigTest(unittest.TestCase):
    def test_development_allows_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=True, auto_create_tables=None)

        self.assertTrue(settings.should_auto_create_tables)

    def test_production_disables_schema_auto_create_by_default(self) -> None:
        settings = Settings(debug=False, auto_create_tables=None)

        self.assertFalse(settings.should_auto_create_tables)

    def test_explicit_auto_create_tables_override_is_respected(self) -> None:
        settings = Settings(debug=False, auto_create_tables=True)

        self.assertTrue(settings.should_auto_create_tables)

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


if __name__ == "__main__":
    unittest.main()
