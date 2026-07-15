"""Catalog-derived regional presentation tests."""

from __future__ import annotations

from pathlib import Path
import unittest

from app.services.billing_catalog_service import (
    BillingProductSnapshot,
    localize_catalog_products,
)


ROOT = Path(__file__).resolve().parents[2]


class RegionalPricingTest(unittest.TestCase):
    def test_localization_formats_but_never_changes_authoritative_amount(self) -> None:
        product = BillingProductSnapshot(
            product_code="pack_50",
            product_kind="credit_pack",
            pre_tax_minor_units=1290,
            currency="USD",
            credits=50,
            retention_tier="paid_90d",
            provider_product_id=None,
            metadata={},
        )

        localized = localize_catalog_products([product], locale="zh-CN")[0]

        self.assertEqual(localized["pre_tax_minor_units"], 1290)
        self.assertEqual(localized["currency"], "USD")
        self.assertEqual(localized["credits"], 50)
        self.assertEqual(localized["display_price"], "$12.90")
        self.assertEqual(localized["region"], "CN")

    def test_credit_service_contains_no_static_catalog_or_fallback_price(self) -> None:
        source = (ROOT / "backend/app/services/credit_service.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CREDIT_PACKAGES =", source)
        self.assertNotIn("get_credit_package_overrides() or", source)


if __name__ == "__main__":
    unittest.main()
