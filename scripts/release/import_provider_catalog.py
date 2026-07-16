"""Validate and append protected Provider product mappings.

The command never prints raw Provider product IDs; diagnostics use SHA-256 only.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.models.billing_catalog import (  # noqa: E402
    BillingCatalogImportAudit,
    BillingCatalogVersion,
    BillingProviderProduct,
)
from app.services.billing_catalog_service import EXPECTED_RELEASE_PRODUCTS  # noqa: E402


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,255}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")


class CatalogMappingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedProviderProduct:
    product_code: str
    provider_product_id: str
    provider_id_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedMapping:
    environment: str
    catalog_version: str
    provider: str
    release_sha: str
    approver_audit_id: str
    products: tuple[ValidatedProviderProduct, ...]


def validate_mapping(payload: object, *, expected_environment: str) -> ValidatedMapping:
    if not isinstance(payload, dict):
        raise CatalogMappingError("mapping_not_object")
    allowed = {
        "schema_version",
        "environment",
        "catalog_version",
        "provider",
        "release_sha",
        "approver_audit_id",
        "products",
    }
    if set(payload) != allowed:
        raise CatalogMappingError("mapping_keys_invalid")
    if payload.get("schema_version") != "provider-product-mapping.v1":
        raise CatalogMappingError("mapping_schema_version_invalid")
    environment = str(payload.get("environment") or "")
    if environment != expected_environment or environment not in {"preview", "production"}:
        raise CatalogMappingError("mapping_environment_mismatch")
    if payload.get("catalog_version") != "2026-07-10":
        raise CatalogMappingError("mapping_catalog_version_invalid")
    if payload.get("provider") != "creem":
        raise CatalogMappingError("mapping_provider_invalid")
    release_sha = str(payload.get("release_sha") or "").lower()
    if not _SHA_PATTERN.fullmatch(release_sha):
        raise CatalogMappingError("mapping_release_sha_invalid")
    approver_audit_id = str(payload.get("approver_audit_id") or "").strip()
    if not _TOKEN_PATTERN.fullmatch(approver_audit_id):
        raise CatalogMappingError("mapping_approval_invalid")
    product_mapping = payload.get("products")
    if not isinstance(product_mapping, dict):
        raise CatalogMappingError("mapping_products_invalid")
    expected_codes = set(EXPECTED_RELEASE_PRODUCTS)
    if set(product_mapping) != expected_codes:
        raise CatalogMappingError("mapping_product_cardinality")
    provider_ids: set[str] = set()
    products: list[ValidatedProviderProduct] = []
    for code in sorted(expected_codes):
        provider_id = str(product_mapping.get(code) or "").strip()
        if not _TOKEN_PATTERN.fullmatch(provider_id):
            raise CatalogMappingError(f"mapping_provider_id_invalid:{code}")
        if provider_id in provider_ids:
            raise CatalogMappingError("mapping_provider_id_duplicate")
        provider_ids.add(provider_id)
        products.append(
            ValidatedProviderProduct(
                product_code=code,
                provider_product_id=provider_id,
                provider_id_sha256=sha256(provider_id.encode("utf-8")).hexdigest(),
            )
        )
    return ValidatedMapping(
        environment=environment,
        catalog_version="2026-07-10",
        provider="creem",
        release_sha=release_sha,
        approver_audit_id=approver_audit_id,
        products=tuple(products),
    )


async def _append_mapping(
    database_url: str,
    mapping: ValidatedMapping,
    *,
    source_sha256: str,
) -> None:
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            versions = list(
                (
                    await db.scalars(
                        select(BillingCatalogVersion).where(
                            BillingCatalogVersion.environment == mapping.environment,
                            BillingCatalogVersion.version == mapping.catalog_version,
                        )
                    )
                ).all()
            )
            if len(versions) != 1:
                raise CatalogMappingError("target_catalog_cardinality")
            version = versions[0]
            existing = list(
                (
                    await db.scalars(
                        select(BillingProviderProduct).where(
                            BillingProviderProduct.catalog_version_id == version.id,
                            BillingProviderProduct.provider == mapping.provider,
                        )
                    )
                ).all()
            )
            if existing:
                raise CatalogMappingError("provider_mapping_already_exists")
            for product in mapping.products:
                db.add(
                    BillingProviderProduct(
                        catalog_version_id=version.id,
                        environment=mapping.environment,
                        provider=mapping.provider,
                        product_code=product.product_code,
                        provider_product_id=product.provider_product_id,
                        provider_id_sha256=product.provider_id_sha256,
                        source_sha256=source_sha256,
                        release_sha=mapping.release_sha,
                        approver_audit_id=mapping.approver_audit_id,
                    )
                )
            db.add(
                BillingCatalogImportAudit(
                    environment=mapping.environment,
                    catalog_version=mapping.catalog_version,
                    provider=mapping.provider,
                    source_sha256=source_sha256,
                    release_sha=mapping.release_sha,
                    approver_audit_id=mapping.approver_audit_id,
                    product_id_hashes={
                        item.product_code: item.provider_id_sha256
                        for item in mapping.products
                    },
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--mapping-file-env", required=True)
    parser.add_argument("--environment", required=True, choices=("preview", "production"))
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--approver-audit-id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mapping_path_value = os.environ.get(args.mapping_file_env, "").strip()
    if not mapping_path_value:
        raise SystemExit(f"{args.mapping_file_env} is required")
    mapping_path = Path(mapping_path_value).resolve()
    raw = mapping_path.read_bytes()
    source_sha = sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8"))
    if args.approver_audit_id:
        payload["approver_audit_id"] = args.approver_audit_id
    mapping = validate_mapping(payload, expected_environment=args.environment)
    if mapping.catalog_version != args.catalog_version:
        raise SystemExit("catalog version mismatch")
    if mapping.release_sha != args.release_sha.lower():
        raise SystemExit("release SHA mismatch")
    summary = {
        "environment": mapping.environment,
        "catalog_version": mapping.catalog_version,
        "provider": mapping.provider,
        "source_sha256": source_sha,
        "provider_id_hashes": {
            item.product_code: item.provider_id_sha256 for item in mapping.products
        },
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        database_url = os.environ.get(args.database_url_env, "").strip()
        if not database_url:
            raise SystemExit(f"{args.database_url_env} is required")
        asyncio.run(_append_mapping(database_url, mapping, source_sha256=source_sha))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
