"""Regional pricing metadata contract tests."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.credit_service import CREDIT_PACKAGES, localize_credit_packages  # noqa: E402


class RegionalPricingTest(unittest.TestCase):
    def test_credit_packages_include_region_ready_metadata(self) -> None:
        packages = localize_credit_packages(CREDIT_PACKAGES, locale="zh")

        self.assertEqual(packages[0]["region"], "CN")
        self.assertEqual(packages[0]["currency"], "USD")
        self.assertEqual(packages[0]["price_cents"], 1290)
        self.assertEqual(packages[0]["display_price"], "$12.90")
        self.assertIn("manual_review", packages[0]["payment_methods"])
        self.assertFalse(packages[0]["localized_pricing_ready"])
        self.assertEqual(packages[0]["refund_policy_url"], "/pages/legal/refund")


if __name__ == "__main__":
    unittest.main()
