"""Remote join feature flag contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.services import ops_config_service  # noqa: E402


class RemoteJoinConfigTest(unittest.TestCase):
    def test_remote_join_is_enabled_by_default_for_web_saas(self) -> None:
        settings = Settings(_env_file=None)

        self.assertTrue(settings.remote_join_enabled)

    def test_public_config_respects_explicit_remote_join_disable(self) -> None:
        original_settings = ops_config_service.settings
        original_default = ops_config_service.DEFAULT_OPS_CONFIG["feature_flags"]["remote_join"]
        try:
            ops_config_service.settings = Settings(_env_file=None, remote_join_enabled=False)
            ops_config_service.DEFAULT_OPS_CONFIG["feature_flags"]["remote_join"] = False

            config = ops_config_service.get_public_ops_config()

            self.assertFalse(config["feature_flags"]["remote_join"])
        finally:
            ops_config_service.settings = original_settings
            ops_config_service.DEFAULT_OPS_CONFIG["feature_flags"]["remote_join"] = original_default


if __name__ == "__main__":
    unittest.main()
