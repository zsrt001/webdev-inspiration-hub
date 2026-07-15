"""Health route contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from tests.route_contract import effective_paths  # noqa: E402


class HealthRoutesTest(unittest.TestCase):
    def test_readiness_route_is_registered(self) -> None:
        paths = effective_paths(app)

        self.assertIn("/health", paths)
        self.assertIn("/health/ready", paths)


if __name__ == "__main__":
    unittest.main()
