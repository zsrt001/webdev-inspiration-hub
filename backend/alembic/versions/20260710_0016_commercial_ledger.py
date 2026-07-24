"""Add versioned catalog, grant lineage, reservations, entitlements and outbox.

Revision ID: 20260710_0016
Revises: 20260710_0015
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0016"
down_revision = "20260710_0015"
branch_labels = None
depends_on = None


COMMERCIAL_TABLES = (
    "billing_catalog_versions",
    "billing_products",
    "billing_provider_products",
    "billing_catalog_import_audits",
    "credit_grant_lots",
    "credit_reservations",
    "credit_reservation_allocations",
    "order_entitlements",
    "order_entitlement_fundings",
    "welcome_grant_claims",
    "payment_reconciliation_cases",
    "idempotency_records",
    "outbox_events",
)


def _create_catalog_tables() -> None:
    op.create_table(
        "billing_catalog_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_sha", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("environment", "version", name="uq_billing_catalog_environment_version"),
        sa.CheckConstraint("expires_at IS NULL OR expires_at > effective_at", name="ck_billing_catalog_version_window"),
    )
    op.create_index("ix_billing_catalog_versions_environment", "billing_catalog_versions", ["environment"])
    op.create_index("ix_billing_catalog_versions_effective_at", "billing_catalog_versions", ["effective_at"])
    op.create_index("ix_billing_catalog_versions_expires_at", "billing_catalog_versions", ["expires_at"])

    op.create_table(
        "billing_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("product_kind", sa.String(32), nullable=False),
        sa.Column("pre_tax_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("retention_tier", sa.String(32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["billing_catalog_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("catalog_version_id", "product_code", name="uq_billing_product_version_code"),
        sa.CheckConstraint("product_kind IN ('credit_pack', 'subscription')", name="ck_billing_products_kind"),
        sa.CheckConstraint("pre_tax_minor_units > 0", name="ck_billing_products_amount_positive"),
        sa.CheckConstraint("credits > 0", name="ck_billing_products_credits_positive"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_billing_products_currency"),
    )
    op.create_index("ix_billing_products_catalog_version_id", "billing_products", ["catalog_version_id"])
    op.create_index("ix_billing_products_product_code", "billing_products", ["product_code"])

    op.create_table(
        "billing_provider_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("provider_product_id", sa.String(256), nullable=False),
        sa.Column("provider_id_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("release_sha", sa.String(64), nullable=False),
        sa.Column("approver_audit_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["billing_catalog_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("environment", "provider", "provider_product_id", name="uq_billing_provider_external_product"),
        sa.UniqueConstraint("catalog_version_id", "provider", "product_code", name="uq_billing_provider_catalog_product"),
    )
    op.create_index("ix_billing_provider_products_catalog_version_id", "billing_provider_products", ["catalog_version_id"])
    op.create_index("ix_billing_provider_products_environment", "billing_provider_products", ["environment"])

    op.create_table(
        "billing_catalog_import_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("release_sha", sa.String(64), nullable=False),
        sa.Column("approver_audit_id", sa.String(128), nullable=False),
        sa.Column("product_id_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("environment", "catalog_version", "provider", "source_sha256", name="uq_billing_catalog_import_audit"),
    )


def _expand_credit_authority() -> None:
    op.add_column("user_credits", sa.Column("reserved_balance", sa.Integer(), server_default="0", nullable=False))
    op.create_check_constraint("ck_user_credits_reserved_nonnegative", "user_credits", "reserved_balance >= 0")
    for name, column in (
        ("root_transaction_id", sa.Column("root_transaction_id", postgresql.UUID(as_uuid=True), nullable=True)),
        ("reversal_of_transaction_id", sa.Column("reversal_of_transaction_id", postgresql.UUID(as_uuid=True), nullable=True)),
        ("request_id", sa.Column("request_id", sa.String(128), nullable=True)),
        ("provider_attempt_id", sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True)),
    ):
        op.add_column("credit_transactions", column)
        op.create_index(f"ix_credit_transactions_{name}", "credit_transactions", [name])
    op.create_foreign_key(
        "fk_credit_transactions_root",
        "credit_transactions",
        "credit_transactions",
        ["root_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_credit_transactions_request_type",
        "credit_transactions",
        ["user_id", "transaction_type", "request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_credit_transactions_reversal",
        "credit_transactions",
        "credit_transactions",
        ["reversal_of_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_ledger_tables() -> None:
    op.create_table(
        "credit_grant_lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("original_amount", sa.Integer(), nullable=False),
        sa.Column("debt_offset_amount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reversed_amount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("frozen_amount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_amount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retention_tier", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["root_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("root_transaction_id", name="uq_credit_grant_lot_root_transaction"),
        sa.CheckConstraint("original_amount > 0", name="ck_credit_grant_lot_original_positive"),
        sa.CheckConstraint("debt_offset_amount >= 0 AND reversed_amount >= 0 AND frozen_amount >= 0 AND consumed_amount >= 0", name="ck_credit_grant_lot_counters_nonnegative"),
        sa.CheckConstraint(
            "debt_offset_amount <= original_amount AND reversed_amount <= original_amount "
            "AND frozen_amount <= original_amount AND consumed_amount <= original_amount",
            name="ck_credit_grant_lot_counters_bounded",
        ),
    )
    op.create_index("ix_credit_grant_lots_user_id", "credit_grant_lots", ["user_id"])
    op.create_index("ix_credit_grant_lots_source_type", "credit_grant_lots", ["source_type"])
    op.create_index("ix_credit_grant_lots_source_id", "credit_grant_lots", ["source_id"])
    op.create_index("ix_credit_grant_lots_expires_at", "credit_grant_lots", ["expires_at"])
    op.create_index("ix_credit_grant_lots_created_at", "credit_grant_lots", ["created_at"])

    op.create_table(
        "credit_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="RESERVED", nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("funding_policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("funding_policy_hash", sa.String(64), nullable=False),
        sa.Column("provider_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("captured_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_retention_tier", sa.String(32), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["captured_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_credit_reservation_user_idempotency"),
        sa.CheckConstraint("amount > 0", name="ck_credit_reservation_amount_positive"),
        sa.CheckConstraint("status IN ('RESERVED','CAPTURED','RELEASED','EXPIRED')", name="ck_credit_reservation_status"),
    )
    op.create_index("ix_credit_reservations_user_id", "credit_reservations", ["user_id"])
    op.create_index("ix_credit_reservations_order_id", "credit_reservations", ["order_id"])
    op.create_index("ix_credit_reservations_provider_attempt_id", "credit_reservations", ["provider_attempt_id"])
    op.create_index("ix_credit_reservations_expires_at", "credit_reservations", ["expires_at"])
    op.create_index(
        "uq_credit_reservations_active_order",
        "credit_reservations",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RESERVED'"),
    )

    op.create_table(
        "credit_reservation_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reservation_id"], ["credit_reservations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grant_lot_id"], ["credit_grant_lots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("reservation_id", "grant_lot_id", name="uq_reservation_allocation_lot"),
        sa.CheckConstraint("amount > 0", name="ck_reservation_allocation_amount_positive"),
    )
    op.create_index("ix_credit_reservation_allocations_reservation_id", "credit_reservation_allocations", ["reservation_id"])
    op.create_index("ix_credit_reservation_allocations_grant_lot_id", "credit_reservation_allocations", ["grant_lot_id"])


def _create_entitlement_and_support_tables() -> None:
    op.create_table(
        "order_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column("access_tier", sa.String(32), nullable=False),
        sa.Column("retention_tier", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reservation_id"], ["credit_reservations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("order_id", name="uq_order_entitlement_order"),
        sa.UniqueConstraint("reservation_id", name="uq_order_entitlements_reservation_id"),
        sa.CheckConstraint("status IN ('ACTIVE','REVOKED')", name="ck_order_entitlement_status"),
    )
    op.create_index("ix_order_entitlements_order_id", "order_entitlements", ["order_id"])
    op.create_index("ix_order_entitlements_user_id", "order_entitlements", ["user_id"])
    op.create_index("ix_order_entitlements_expires_at", "order_entitlements", ["expires_at"])

    op.create_table(
        "order_entitlement_fundings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entitlement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_allocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["entitlement_id"], ["order_entitlements.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reservation_allocation_id"], ["credit_reservation_allocations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grant_lot_id"], ["credit_grant_lots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("reservation_allocation_id", name="uq_entitlement_funding_allocation"),
        sa.CheckConstraint("amount > 0", name="ck_entitlement_funding_amount_positive"),
    )
    op.create_index("ix_order_entitlement_fundings_entitlement_id", "order_entitlement_fundings", ["entitlement_id"])
    op.create_index("ix_order_entitlement_fundings_grant_lot_id", "order_entitlement_fundings", ["grant_lot_id"])

    op.create_table(
        "welcome_grant_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_identity_id"], ["user_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["credit_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grant_lot_id"], ["credit_grant_lots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_identity_id", name="uq_welcome_grant_claim_identity"),
        sa.UniqueConstraint("user_id", name="uq_welcome_grant_claim_user"),
        sa.UniqueConstraint("credit_transaction_id", name="uq_welcome_grant_claim_transaction"),
        sa.UniqueConstraint("grant_lot_id", name="uq_welcome_grant_claim_lot"),
    )
    op.create_index("ix_welcome_grant_claims_user_id", "welcome_grant_claims", ["user_id"])

    op.create_table(
        "payment_reconciliation_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("case_key", sa.String(128), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("raw_payload_sha256", sa.String(64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("provider", "case_key", name="uq_payment_reconciliation_provider_key"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_payment_reconciliation_attempts"),
    )
    op.create_index("ix_payment_reconciliation_cases_user_id", "payment_reconciliation_cases", ["user_id"])
    op.create_index("ix_payment_reconciliation_cases_next_attempt_at", "payment_reconciliation_cases", ["next_attempt_at"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), server_default="STARTED", nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "endpoint", "idempotency_key", name="uq_idempotency_scope_key"),
        sa.CheckConstraint("response_status IS NULL OR response_status BETWEEN 100 AND 599", name="ck_idempotency_response_status"),
    )
    op.create_index("ix_idempotency_records_user_id", "idempotency_records", ["user_id"])
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("dedupe_key", sa.String(192), nullable=False),
        sa.Column("payload_version", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_event_dedupe_key"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_event_attempts"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_outbox_event_fencing"),
        sa.CheckConstraint("(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) OR (lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_outbox_event_lease_coherent"),
    )
    op.create_index("ix_outbox_events_aggregate_type", "outbox_events", ["aggregate_type"])
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_next_attempt_at", "outbox_events", ["next_attempt_at"])


def _seed_release_catalog() -> None:
    version_id = "c7160000-0000-4000-8000-000000000001"
    op.execute(
        sa.text(
            "INSERT INTO billing_catalog_versions "
            "(id, version, environment, effective_at, release_sha) VALUES "
            "(CAST(:id AS uuid), '2026-07-10', 'production', '2026-07-10T00:00:00Z', :sha), "
            "('c7160000-0000-4000-8000-000000000002', '2026-07-10', 'preview', "
            "'2026-07-10T00:00:00Z', :sha)"
        ).bindparams(id=version_id, sha="0000000000000000000000000000000000000000")
    )
    products = (
        ("pack_50", "credit_pack", 1290, 50, "paid_90d"),
        ("pack_120", "credit_pack", 2490, 120, "paid_90d"),
        ("pack_300", "credit_pack", 4990, 300, "paid_90d"),
        ("starter_monthly", "subscription", 1900, 80, "subscription_180d"),
        ("creator_monthly", "subscription", 4900, 300, "subscription_180d"),
        ("studio_monthly", "subscription", 12900, 900, "studio_365d"),
    )
    for version_index, catalog_id in enumerate((version_id, "c7160000-0000-4000-8000-000000000002"), start=1):
        for product_index, (code, kind, amount, credits, retention) in enumerate(products, start=1):
            row_id = f"c716{version_index:04d}-0000-4000-8000-{product_index:012d}"
            op.execute(
                sa.text(
                    "INSERT INTO billing_products "
                    "(id, catalog_version_id, product_code, product_kind, pre_tax_minor_units, "
                    "currency, credits, retention_tier, metadata_json) VALUES "
                    "(CAST(:id AS uuid), CAST(:catalog_id AS uuid), :code, :kind, :amount, "
                    "'USD', :credits, :retention, '{}'::jsonb)"
                ).bindparams(
                    id=row_id,
                    catalog_id=catalog_id,
                    code=code,
                    kind=kind,
                    amount=amount,
                    credits=credits,
                    retention=retention,
                )
            )


def _create_commercial_append_only_guard() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_append_only_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_append_only_guard$
            BEGIN
                RAISE EXCEPTION 'commercial fact is append-only' USING ERRCODE = '23514';
            END;
            $commercial_append_only_guard$;
            """
        )
    )
    for table in (
        "billing_catalog_versions",
        "billing_products",
        "billing_provider_products",
        "billing_catalog_import_audits",
        "credit_transactions",
        "credit_reservation_allocations",
        "order_entitlement_fundings",
        "welcome_grant_claims",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON public.{table} "
                "FOR EACH ROW EXECUTE FUNCTION public.commercial_append_only_guard()"
            )
        )


def _create_commercial_transition_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_reservation_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_reservation_transition_guard$
            BEGIN
                IF OLD.user_id <> NEW.user_id
                   OR OLD.order_id <> NEW.order_id
                   OR OLD.amount <> NEW.amount
                   OR OLD.idempotency_key <> NEW.idempotency_key
                   OR OLD.request_hash <> NEW.request_hash
                   OR OLD.funding_policy_hash <> NEW.funding_policy_hash
                   OR OLD.funding_policy_snapshot <> NEW.funding_policy_snapshot
                   OR OLD.expires_at <> NEW.expires_at THEN
                    RAISE EXCEPTION 'reservation immutable field changed' USING ERRCODE = '23514';
                END IF;
                IF OLD.status <> 'RESERVED'
                   OR NEW.status NOT IN ('CAPTURED', 'RELEASED', 'EXPIRED') THEN
                    RAISE EXCEPTION 'invalid reservation transition' USING ERRCODE = '23514';
                END IF;
                IF NEW.status = 'CAPTURED'
                   AND (NEW.provider_attempt_id IS NULL OR NEW.captured_transaction_id IS NULL
                        OR NEW.captured_at IS NULL OR NEW.captured_retention_tier IS NULL) THEN
                    RAISE EXCEPTION 'captured reservation lineage incomplete' USING ERRCODE = '23514';
                END IF;
                IF NEW.status IN ('RELEASED', 'EXPIRED') AND NEW.released_at IS NULL THEN
                    RAISE EXCEPTION 'released reservation timestamp missing' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_reservation_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_credit_reservations_transition
            BEFORE UPDATE ON public.credit_reservations
            FOR EACH ROW EXECUTE FUNCTION public.commercial_reservation_transition_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_entitlement_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_entitlement_transition_guard$
            BEGIN
                IF OLD.order_id <> NEW.order_id
                   OR OLD.user_id <> NEW.user_id
                   OR OLD.reservation_id <> NEW.reservation_id
                   OR OLD.access_tier <> NEW.access_tier
                   OR OLD.retention_tier <> NEW.retention_tier
                   OR NEW.expires_at < OLD.expires_at
                   OR OLD.status <> 'ACTIVE'
                   OR NEW.status <> 'REVOKED'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoke_reason IS NULL THEN
                    RAISE EXCEPTION 'invalid entitlement transition' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_entitlement_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_order_entitlements_transition
            BEFORE UPDATE ON public.order_entitlements
            FOR EACH ROW EXECUTE FUNCTION public.commercial_entitlement_transition_guard();
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_reservation_allocation_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_reservation_allocation_guard$
            DECLARE
                target_id uuid;
                required_amount integer;
                allocated_amount bigint;
            BEGIN
                IF TG_TABLE_NAME = 'credit_reservations' THEN
                    target_id := COALESCE(NEW.id, OLD.id);
                ELSIF TG_TABLE_NAME = 'credit_reservation_allocations' THEN
                    target_id := COALESCE(NEW.reservation_id, OLD.reservation_id);
                ELSE
                    RAISE EXCEPTION 'unexpected reservation allocation guard table: %',
                        TG_TABLE_NAME USING ERRCODE = '23514';
                END IF;
                SELECT amount INTO required_amount FROM public.credit_reservations WHERE id = target_id;
                IF required_amount IS NULL THEN
                    RETURN NULL;
                END IF;
                SELECT COALESCE(SUM(amount), 0) INTO allocated_amount
                FROM public.credit_reservation_allocations WHERE reservation_id = target_id;
                IF allocated_amount <> required_amount THEN
                    RAISE EXCEPTION 'reservation allocation sum mismatch' USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END;
            $commercial_reservation_allocation_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_credit_reservation_allocation_sum_from_reservation
            AFTER INSERT OR UPDATE ON public.credit_reservations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.commercial_reservation_allocation_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_credit_reservation_allocation_sum_from_allocation
            AFTER INSERT OR UPDATE OR DELETE ON public.credit_reservation_allocations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.commercial_reservation_allocation_guard();
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_entitlement_funding_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_entitlement_funding_guard$
            DECLARE
                target_id uuid;
                target_reservation_id uuid;
                reservation_status text;
                reservation_amount integer;
                funding_amount bigint;
                mismatch_count bigint;
            BEGIN
                IF TG_TABLE_NAME = 'order_entitlements' THEN
                    target_id := COALESCE(NEW.id, OLD.id);
                ELSIF TG_TABLE_NAME = 'order_entitlement_fundings' THEN
                    target_id := COALESCE(NEW.entitlement_id, OLD.entitlement_id);
                ELSE
                    RAISE EXCEPTION 'unexpected entitlement funding guard table: %',
                        TG_TABLE_NAME USING ERRCODE = '23514';
                END IF;
                SELECT reservation_id INTO target_reservation_id
                FROM public.order_entitlements WHERE id = target_id;
                IF target_reservation_id IS NULL THEN
                    RETURN NULL;
                END IF;
                SELECT status, amount INTO reservation_status, reservation_amount
                FROM public.credit_reservations WHERE id = target_reservation_id;
                SELECT COALESCE(SUM(amount), 0) INTO funding_amount
                FROM public.order_entitlement_fundings WHERE entitlement_id = target_id;
                SELECT COUNT(*) INTO mismatch_count
                FROM public.order_entitlement_fundings funding
                LEFT JOIN public.credit_reservation_allocations allocation
                  ON allocation.id = funding.reservation_allocation_id
                 AND allocation.reservation_id = target_reservation_id
                 AND allocation.grant_lot_id = funding.grant_lot_id
                 AND allocation.amount = funding.amount
                WHERE funding.entitlement_id = target_id AND allocation.id IS NULL;
                IF reservation_status <> 'CAPTURED'
                   OR funding_amount <> reservation_amount
                   OR mismatch_count <> 0 THEN
                    RAISE EXCEPTION 'entitlement funding does not reproduce capture' USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END;
            $commercial_entitlement_funding_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_order_entitlement_funding_from_entitlement
            AFTER INSERT OR UPDATE ON public.order_entitlements
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.commercial_entitlement_funding_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_order_entitlement_funding_from_funding
            AFTER INSERT OR UPDATE OR DELETE ON public.order_entitlement_fundings
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.commercial_entitlement_funding_guard();
            """
        )
    )


def _create_commercial_rls() -> None:
    for table in COMMERCIAL_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))


def upgrade() -> None:
    _create_catalog_tables()
    _expand_credit_authority()
    _create_ledger_tables()
    _create_entitlement_and_support_tables()
    _seed_release_catalog()
    _create_commercial_append_only_guard()
    _create_commercial_transition_guards()
    _create_commercial_rls()


def downgrade() -> None:
    for table in reversed(COMMERCIAL_TABLES):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON public.{table}"))
        op.drop_table(table)
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_transactions_append_only ON public.credit_transactions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_reservation_allocation_guard()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_entitlement_funding_guard()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_entitlement_transition_guard()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_reservation_transition_guard()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_append_only_guard()"))
    op.drop_index("uq_credit_transactions_request_type", table_name="credit_transactions")
    op.drop_constraint("fk_credit_transactions_reversal", "credit_transactions", type_="foreignkey")
    op.drop_constraint("fk_credit_transactions_root", "credit_transactions", type_="foreignkey")
    for name in ("provider_attempt_id", "request_id", "reversal_of_transaction_id", "root_transaction_id"):
        op.drop_index(f"ix_credit_transactions_{name}", table_name="credit_transactions")
        op.drop_column("credit_transactions", name)
    op.drop_constraint("ck_user_credits_reserved_nonnegative", "user_credits", type_="check")
    op.drop_column("user_credits", "reserved_balance")
