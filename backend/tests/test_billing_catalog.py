"""Authoritative versioned billing catalog tests."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException

from app.models.billing_catalog import BillingCatalogVersion, BillingProduct
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    EXPECTED_RELEASE_PRODUCTS,
    load_active_catalog,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _Db:
    def __init__(self, *results):
        self.results = deque(results)

    async def scalars(self, _statement):
        return _Rows(self.results.popleft())


def _version() -> BillingCatalogVersion:
    return BillingCatalogVersion(
        id=uuid4(),
        version="2026-07-10",
        environment="production",
        effective_at=NOW - timedelta(days=1),
        release_sha="a" * 40,
    )


def _products(version_id):
    rows = []
    for code, contract in EXPECTED_RELEASE_PRODUCTS.items():
        rows.append(
            BillingProduct(
                id=uuid4(),
                catalog_version_id=version_id,
                product_code=code,
                product_kind=contract.product_kind,
                pre_tax_minor_units=contract.pre_tax_minor_units,
                currency=contract.currency,
                credits=contract.credits,
                retention_tier=contract.retention_tier,
                metadata_json={},
            )
        )
    return rows


class BillingCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_conflicting_catalog_fails_closed(self) -> None:
        with self.assertRaises(BillingCatalogUnavailable):
            await load_active_catalog(_Db([]), environment="production", now=NOW)
        with self.assertRaises(BillingCatalogUnavailable):
            await load_active_catalog(
                _Db([_version(), _version()]),
                environment="production",
                now=NOW,
            )

    async def test_seeded_catalog_is_exact(self) -> None:
        version = _version()
        catalog = await load_active_catalog(
            _Db([version], _products(version.id)),
            environment="production",
            now=NOW,
        )

        actual = {
            item.product_code: (
                item.pre_tax_minor_units,
                item.currency,
                item.credits,
            )
            for item in catalog.products
        }
        expected = {
            code: (
                item.pre_tax_minor_units,
                item.currency,
                item.credits,
            )
            for code, item in EXPECTED_RELEASE_PRODUCTS.items()
        }
        self.assertEqual(actual, expected)

    async def test_missing_or_duplicate_product_fails_closed(self) -> None:
        version = _version()
        products = _products(version.id)
        with self.assertRaises(BillingCatalogUnavailable):
            await load_active_catalog(
                _Db([version], products[:-1]),
                environment="production",
                now=NOW,
            )
        with self.assertRaises(BillingCatalogUnavailable):
            await load_active_catalog(
                _Db([version], [*products, products[0]]),
                environment="production",
                now=NOW,
            )

    async def test_packages_route_reads_catalog_and_fails_closed_without_it(self) -> None:
        from app.routers import credits

        version = _version()
        snapshot_db = _Db([version], _products(version.id))
        with patch.object(credits.settings, "runtime_environment", "production"):
            response = await credits.list_packages(
                region=None,
                locale="en-US",
                db=snapshot_db,
            )
        self.assertEqual(len(response.packages), len(EXPECTED_RELEASE_PRODUCTS))
        self.assertEqual(
            {item.id: item.price_cents for item in response.packages},
            {
                code: contract.pre_tax_minor_units
                for code, contract in EXPECTED_RELEASE_PRODUCTS.items()
            },
        )

        with (
            patch.object(credits.settings, "runtime_environment", "production"),
            self.assertRaises(HTTPException) as raised,
        ):
            await credits.list_packages(region=None, locale=None, db=_Db([]))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "credit_catalog_unavailable")


if __name__ == "__main__":
    unittest.main()
