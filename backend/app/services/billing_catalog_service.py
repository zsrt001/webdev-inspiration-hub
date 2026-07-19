"""Authoritative PostgreSQL billing catalog loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from types import MappingProxyType
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.billing_catalog import (
    BillingCatalogVersion,
    BillingProduct,
    BillingProviderProduct,
)


logger = logging.getLogger(__name__)
settings = get_settings()


class BillingCatalogUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BillingCatalogMismatch(BillingCatalogUnavailable):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseProductContract:
    product_kind: str
    pre_tax_minor_units: int
    currency: str
    credits: int
    retention_tier: str


EXPECTED_RELEASE_PRODUCTS = MappingProxyType(
    {
        "pack_50": ReleaseProductContract("credit_pack", 1290, "USD", 50, "paid_90d"),
        "pack_120": ReleaseProductContract("credit_pack", 2490, "USD", 120, "paid_90d"),
        "pack_300": ReleaseProductContract("credit_pack", 4990, "USD", 300, "paid_90d"),
        "starter_monthly": ReleaseProductContract("subscription", 1900, "USD", 80, "subscription_180d"),
        "creator_monthly": ReleaseProductContract("subscription", 4900, "USD", 300, "subscription_180d"),
        "studio_monthly": ReleaseProductContract("subscription", 12900, "USD", 900, "studio_365d"),
    }
)

_FORBIDDEN_PROMISE_KEYS = {
    "remote_join",
    "live_portrait",
    "priority_generation",
    "trial",
    "upgrade",
    "downgrade",
    "pause",
    "proration",
    "email_promise",
}


@dataclass(frozen=True, slots=True)
class BillingProductSnapshot:
    product_code: str
    product_kind: str
    pre_tax_minor_units: int
    currency: str
    credits: int
    retention_tier: str
    provider_product_id: str | None
    metadata: dict


@dataclass(frozen=True, slots=True)
class BillingCatalogSnapshot:
    catalog_id: str
    version: str
    environment: str
    effective_at: datetime
    expires_at: datetime | None
    release_sha: str
    products: tuple[BillingProductSnapshot, ...]

    @property
    def by_code(self) -> dict[str, BillingProductSnapshot]:
        return {product.product_code: product for product in self.products}

    def assert_exact_release_contract(self) -> None:
        by_code = self.by_code
        if len(by_code) != len(self.products) or set(by_code) != set(EXPECTED_RELEASE_PRODUCTS):
            raise BillingCatalogUnavailable("catalog_product_cardinality")
        for code, expected in EXPECTED_RELEASE_PRODUCTS.items():
            actual = by_code[code]
            if (
                actual.product_kind != expected.product_kind
                or actual.pre_tax_minor_units != expected.pre_tax_minor_units
                or actual.currency != expected.currency
                or actual.credits != expected.credits
                or actual.retention_tier != expected.retention_tier
            ):
                raise BillingCatalogUnavailable(f"catalog_product_mismatch:{code}")
            if _FORBIDDEN_PROMISE_KEYS & set(actual.metadata):
                raise BillingCatalogUnavailable(f"catalog_unimplemented_promise:{code}")

    @classmethod
    def from_rows(
        cls,
        version: BillingCatalogVersion,
        products: list[BillingProduct],
    ) -> "BillingCatalogSnapshot":
        snapshot = cls(
            catalog_id=str(version.id),
            version=version.version,
            environment=version.environment,
            effective_at=version.effective_at,
            expires_at=version.expires_at,
            release_sha=version.release_sha,
            products=tuple(
                BillingProductSnapshot(
                    product_code=row.product_code,
                    product_kind=row.product_kind,
                    pre_tax_minor_units=int(row.pre_tax_minor_units),
                    currency=str(row.currency).upper(),
                    credits=int(row.credits),
                    retention_tier=row.retention_tier,
                    provider_product_id=None,
                    metadata=dict(row.metadata_json or {}),
                )
                for row in sorted(products, key=lambda item: item.product_code)
            ),
        )
        snapshot.assert_exact_release_contract()
        return snapshot


@dataclass(frozen=True, slots=True)
class CheckoutCatalogSelection:
    catalog_version_id: uuid.UUID
    catalog_version: str
    release_sha: str
    product: BillingProductSnapshot

    def as_snapshot(self) -> dict:
        return {
            "catalog_version_id": str(self.catalog_version_id),
            "catalog_version": self.catalog_version,
            "release_sha": self.release_sha,
            "product_code": self.product.product_code,
            "product_kind": self.product.product_kind,
            "provider_product_id": self.product.provider_product_id,
            "pre_tax_minor_units": self.product.pre_tax_minor_units,
            "currency": self.product.currency,
            "credits": self.product.credits,
            "retention_tier": self.product.retention_tier,
        }


async def load_active_catalog(
    db: AsyncSession,
    *,
    environment: str,
    now: datetime | None = None,
) -> BillingCatalogSnapshot:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("catalog time must be timezone-aware")
    versions = list(
        (
            await db.scalars(
                select(BillingCatalogVersion).where(
                    BillingCatalogVersion.environment == environment,
                    BillingCatalogVersion.effective_at <= current,
                    or_(
                        BillingCatalogVersion.expires_at.is_(None),
                        BillingCatalogVersion.expires_at > current,
                    ),
                )
            )
        ).all()
    )
    if len(versions) != 1:
        logger.error(
            "billing_catalog_unavailable code=active_catalog_cardinality environment=%s count=%s",
            environment,
            len(versions),
        )
        raise BillingCatalogUnavailable("active_catalog_cardinality")
    version = versions[0]
    products = list(
        (
            await db.scalars(
                select(BillingProduct).where(
                    BillingProduct.catalog_version_id == version.id
                )
            )
        ).all()
    )
    try:
        return BillingCatalogSnapshot.from_rows(version, products)
    except BillingCatalogUnavailable:
        logger.exception(
            "billing_catalog_unavailable code=release_contract environment=%s version=%s",
            environment,
            version.version,
        )
        raise


async def require_catalog_product(
    db: AsyncSession,
    *,
    product_code: str,
    provider_product_id: str,
    pre_tax_minor_units: int,
    currency: str,
    environment: str | None = None,
    provider: str = "creem",
) -> BillingProductSnapshot:
    target_environment = environment or settings.runtime_environment
    catalog = await load_active_catalog(db, environment=target_environment)
    product = catalog.by_code.get(str(product_code))
    if product is None:
        raise BillingCatalogUnavailable("product_not_active")
    mapping_rows = list(
        (
            await db.scalars(
                select(BillingProviderProduct).where(
                    BillingProviderProduct.catalog_version_id == uuid.UUID(catalog.catalog_id),
                    BillingProviderProduct.environment == target_environment,
                    BillingProviderProduct.provider == provider,
                    BillingProviderProduct.product_code == product.product_code,
                )
            )
        ).all()
    )
    if len(mapping_rows) != 1:
        raise BillingCatalogUnavailable("provider_product_mapping_cardinality")
    mapping = mapping_rows[0]
    if (
        mapping.provider_product_id != provider_product_id
        or product.pre_tax_minor_units != int(pre_tax_minor_units)
        or product.currency != str(currency).upper()
    ):
        raise BillingCatalogMismatch(product.product_code)
    return replace(product, provider_product_id=mapping.provider_product_id)


async def require_checkout_catalog_product(
    db: AsyncSession,
    *,
    product_code: str,
    environment: str | None = None,
    provider: str = "creem",
) -> CheckoutCatalogSelection:
    """Load one active credit pack and its release-bound Provider mapping."""

    target_environment = environment or settings.runtime_environment
    catalog = await load_active_catalog(db, environment=target_environment)
    product = catalog.by_code.get(str(product_code).strip())
    if product is None:
        raise BillingCatalogUnavailable("product_not_active")
    if product.product_kind != "credit_pack":
        raise BillingCatalogUnavailable("checkout_product_kind_invalid")
    mappings = list(
        (
            await db.scalars(
                select(BillingProviderProduct).where(
                    BillingProviderProduct.catalog_version_id == uuid.UUID(catalog.catalog_id),
                    BillingProviderProduct.environment == target_environment,
                    BillingProviderProduct.provider == provider,
                    BillingProviderProduct.product_code == product.product_code,
                )
            )
        ).all()
    )
    if len(mappings) != 1:
        raise BillingCatalogUnavailable("provider_product_mapping_cardinality")
    provider_product_id = str(mappings[0].provider_product_id or "").strip()
    if not provider_product_id:
        raise BillingCatalogUnavailable("provider_product_mapping_empty")
    return CheckoutCatalogSelection(
        catalog_version_id=uuid.UUID(catalog.catalog_id),
        catalog_version=catalog.version,
        release_sha=catalog.release_sha,
        product=replace(product, provider_product_id=provider_product_id),
    )


async def require_subscription_checkout_catalog_product(
    db: AsyncSession,
    *,
    product_code: str,
    environment: str | None = None,
    provider: str = "creem",
) -> CheckoutCatalogSelection:
    """Load one active subscription product and its release-bound Provider mapping."""

    target_environment = environment or settings.runtime_environment
    catalog = await load_active_catalog(db, environment=target_environment)
    product = catalog.by_code.get(str(product_code).strip())
    if product is None:
        raise BillingCatalogUnavailable("product_not_active")
    if product.product_kind != "subscription":
        raise BillingCatalogUnavailable("subscription_checkout_product_kind_invalid")
    mappings = list(
        (
            await db.scalars(
                select(BillingProviderProduct).where(
                    BillingProviderProduct.catalog_version_id
                    == uuid.UUID(catalog.catalog_id),
                    BillingProviderProduct.environment == target_environment,
                    BillingProviderProduct.provider == provider,
                    BillingProviderProduct.product_code == product.product_code,
                )
            )
        ).all()
    )
    if len(mappings) != 1:
        raise BillingCatalogUnavailable("provider_product_mapping_cardinality")
    provider_product_id = str(mappings[0].provider_product_id or "").strip()
    if not provider_product_id:
        raise BillingCatalogUnavailable("provider_product_mapping_empty")
    return CheckoutCatalogSelection(
        catalog_version_id=uuid.UUID(catalog.catalog_id),
        catalog_version=catalog.version,
        release_sha=catalog.release_sha,
        product=replace(product, provider_product_id=provider_product_id),
    )


async def require_subscription_catalog_product(
    db: AsyncSession,
    *,
    provider_product_id: str,
    pre_tax_minor_units: int,
    currency: str,
    environment: str | None = None,
    provider: str = "creem",
) -> CheckoutCatalogSelection:
    """Resolve an exact active subscription product from Provider facts."""

    target_environment = environment or settings.runtime_environment
    catalog = await load_active_catalog(db, environment=target_environment)
    mapping_rows = list(
        (
            await db.scalars(
                select(BillingProviderProduct).where(
                    BillingProviderProduct.catalog_version_id == uuid.UUID(catalog.catalog_id),
                    BillingProviderProduct.environment == target_environment,
                    BillingProviderProduct.provider == provider,
                    BillingProviderProduct.provider_product_id == str(provider_product_id),
                )
            )
        ).all()
    )
    if len(mapping_rows) != 1:
        raise BillingCatalogUnavailable("provider_product_mapping_cardinality")
    mapping = mapping_rows[0]
    product = catalog.by_code.get(mapping.product_code)
    if product is None or product.product_kind != "subscription":
        raise BillingCatalogUnavailable("subscription_product_not_active")
    if (
        product.pre_tax_minor_units != int(pre_tax_minor_units)
        or product.currency != str(currency).upper()
    ):
        raise BillingCatalogMismatch(product.product_code)
    return CheckoutCatalogSelection(
        catalog_version_id=uuid.UUID(catalog.catalog_id),
        catalog_version=catalog.version,
        release_sha=catalog.release_sha,
        product=replace(product, provider_product_id=mapping.provider_product_id),
    )


def localize_catalog_products(
    products: list[BillingProductSnapshot] | tuple[BillingProductSnapshot, ...],
    *,
    locale: str | None = None,
) -> list[dict]:
    normalized_locale = str(locale or "en-US").lower()
    region = "CN" if normalized_locale.startswith("zh") else "US"
    localized: list[dict] = []
    for product in products:
        if product.currency != "USD":
            raise BillingCatalogUnavailable("unsupported_display_currency")
        localized.append(
            {
                "id": product.product_code,
                "product_code": product.product_code,
                "product_kind": product.product_kind,
                "pre_tax_minor_units": product.pre_tax_minor_units,
                "price_cents": product.pre_tax_minor_units,
                "currency": product.currency,
                "credits": product.credits,
                "retention_tier": product.retention_tier,
                "display_price": f"${product.pre_tax_minor_units / 100:.2f}",
                "region": region,
                "localized_pricing_ready": False,
            }
        )
    return localized
