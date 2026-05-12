"""Order route contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


class OrderRoutesTest(unittest.TestCase):
    def test_order_list_supports_slash_and_no_slash_paths(self) -> None:
        paths = {route.path for route in app.routes}

        self.assertIn("/api/v1/orders", paths)
        self.assertIn("/api/v1/orders/", paths)


if __name__ == "__main__":
    unittest.main()
