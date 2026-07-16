"""Immutable versioned billing catalog and Provider mapping facts."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BillingCatalogVersion(Base):
    __tablename__ = "billing_catalog_versions"
    __table_args__ = (
        UniqueConstraint("environment", "version", name="uq_billing_catalog_environment_version"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_billing_catalog_version_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    release_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BillingProduct(Base):
    __tablename__ = "billing_products"
    __table_args__ = (
        UniqueConstraint("catalog_version_id", "product_code", name="uq_billing_product_version_code"),
        CheckConstraint(
            "product_kind IN ('credit_pack', 'subscription')",
            name="ck_billing_products_kind",
        ),
        CheckConstraint("pre_tax_minor_units > 0", name="ck_billing_products_amount_positive"),
        CheckConstraint("credits > 0", name="ck_billing_products_credits_positive"),
        CheckConstraint("char_length(currency) = 3", name="ck_billing_products_currency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    pre_tax_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BillingProviderProduct(Base):
    __tablename__ = "billing_provider_products"
    __table_args__ = (
        UniqueConstraint(
            "environment", "provider", "provider_product_id",
            name="uq_billing_provider_external_product",
        ),
        UniqueConstraint(
            "catalog_version_id", "provider", "product_code",
            name="uq_billing_provider_catalog_product",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_product_id: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_id_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    release_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_audit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BillingCatalogImportAudit(Base):
    __tablename__ = "billing_catalog_import_audits"
    __table_args__ = (
        UniqueConstraint(
            "environment", "catalog_version", "provider", "source_sha256",
            name="uq_billing_catalog_import_audit",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    release_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_audit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    product_id_hashes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
