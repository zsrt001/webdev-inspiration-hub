"""Remote join feature flag contract tests."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services import ops_config_service  # noqa: E402


class RemoteJoinConfigTest(unittest.TestCase):
    def test_remote_join_is_disabled_by_default_during_bootstrap_lockdown(self) -> None:
        settings = Settings(_env_file=None)

        self.assertFalse(settings.remote_join_enabled)

    def test_external_ops_config_cannot_publish_retired_feature_flags(self) -> None:
        unsafe_config = {
            **ops_config_service.DEFAULT_OPS_CONFIG,
            "feature_flags": {
                "live_portrait": True,
                "remote_join": True,
                "local_recommendations": True,
                "director_mode": True,
            },
        }

        with patch.object(ops_config_service, "get_ops_config", return_value=unsafe_config):
            config = ops_config_service.get_public_ops_config()

        self.assertNotIn("feature_flags", config)

    def test_normalization_drops_retired_config_sections(self) -> None:
        normalized = ops_config_service._normalize_config(
            {
                **ops_config_service.DEFAULT_OPS_CONFIG,
                "feature_flags": {"remote_join": True},
                "recommendations": {"manual_boosts": {"retired": 100}},
                "crm": {"enabled": True},
            }
        )

        self.assertNotIn("feature_flags", normalized)
        self.assertNotIn("recommendations", normalized)
        self.assertNotIn("crm", normalized)

    def test_public_config_hides_google_oauth_until_exchange_is_configured(self) -> None:
        original_settings = ops_config_service.settings
        try:
            ops_config_service.settings = Settings(
                _env_file=None,
                supabase_url="https://example.supabase.co",
                supabase_anon_key="",
                supabase_jwt_secret="",
            )

            config = ops_config_service.get_public_ops_config()

            self.assertFalse(config["auth"]["google_oauth_enabled"])
            self.assertEqual(config["auth"]["supabase_url"], "")
            self.assertEqual(config["auth"]["supabase_publishable_key"], "")
        finally:
            ops_config_service.settings = original_settings

    def test_public_config_exposes_google_oauth_when_exchange_is_configured(self) -> None:
        original_settings = ops_config_service.settings
        try:
            ops_config_service.settings = Settings(
                _env_file=None,
                supabase_url="https://example.supabase.co",
                supabase_anon_key="anon-key",
                google_auth_enabled=True,
            )

            config = ops_config_service.get_public_ops_config()

            self.assertTrue(config["auth"]["google_oauth_enabled"])
            self.assertEqual(config["auth"]["supabase_url"], "https://example.supabase.co")
            self.assertEqual(config["auth"]["supabase_publishable_key"], "anon-key")
        finally:
            ops_config_service.settings = original_settings

    def test_public_config_uses_retention_home_banner(self) -> None:
        config = ops_config_service.get_public_ops_config()

        self.assertEqual(config["placements"]["home_banner"]["image_url"], "/style-previews/couple_old_money.jpg")

    def test_public_config_normalizes_legacy_heavy_home_banner(self) -> None:
        legacy_config = {
            **ops_config_service.DEFAULT_OPS_CONFIG,
            "placements": {
                "home_banner": {
                    **ops_config_service.DEFAULT_OPS_CONFIG["placements"]["home_banner"],
                    "image_url": "/style-previews/couple_royal_castle.jpg",
                }
            },
        }

        with patch.object(ops_config_service, "get_ops_config", return_value=legacy_config):
            config = ops_config_service.get_public_ops_config()

        self.assertEqual(config["placements"]["home_banner"]["image_url"], "/style-previews/couple_old_money.jpg")


if __name__ == "__main__":
    unittest.main()
