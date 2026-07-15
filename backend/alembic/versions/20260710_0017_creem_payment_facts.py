"""Add durable Creem checkout intents and immutable monetary facts.

Revision ID: 20260710_0017
Revises: 20260710_0016
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0017"
down_revision = "20260710_0016"
branch_labels = None
depends_on = None


PAYMENT_FACT_TABLES = (
    "payment_capture_facts",
    "payment_refund_facts",
    "payment_dispute_facts",
)


def _expand_purchase_intents() -> None:
    op.add_column("credit_purchases", sa.Column("intent_state", sa.String(32), nullable=True))
    op.add_column("credit_purchases", sa.Column("request_hash", sa.String(64), nullable=True))
    op.add_column(
        "credit_purchases",
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("credit_purchases", sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column(
        "credit_purchases",
        sa.Column("internal_metadata_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("credit_purchases", sa.Column("stored_response", postgresql.JSONB(), nullable=True))
    op.add_column(
        "credit_purchases",
        sa.Column("captured_minor_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("tax_minor_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("refunded_minor_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("disputed_minor_units", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("dispute_state", sa.String(32), server_default="NONE", nullable=False),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("grant_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("call_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "credit_purchases",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE credit_purchases SET intent_state = "
            "CASE WHEN status IN ('paid', 'completed') THEN 'CONFIRMED' ELSE 'UNKNOWN' END"
        )
    )
    op.alter_column(
        "credit_purchases",
        "intent_state",
        nullable=False,
        server_default="NEW",
    )
    op.create_foreign_key(
        "fk_credit_purchases_catalog_version",
        "credit_purchases",
        "billing_catalog_versions",
        ["catalog_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_credit_purchases_grant_transaction",
        "credit_purchases",
        "credit_transactions",
        ["grant_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_credit_purchases_grant_lot",
        "credit_purchases",
        "credit_grant_lots",
        ["grant_lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_credit_purchase_internal_metadata",
        "credit_purchases",
        ["internal_metadata_id"],
    )
    op.create_unique_constraint(
        "uq_credit_purchase_grant_transaction",
        "credit_purchases",
        ["grant_transaction_id"],
    )
    op.create_unique_constraint(
        "uq_credit_purchase_grant_lot",
        "credit_purchases",
        ["grant_lot_id"],
    )
    op.create_check_constraint(
        "ck_credit_purchase_intent_state",
        "credit_purchases",
        "intent_state IN ('NEW','CALLING','READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED')",
    )
    op.create_check_constraint(
        "ck_credit_purchase_money_nonnegative",
        "credit_purchases",
        "captured_minor_units >= 0 AND tax_minor_units >= 0 "
        "AND refunded_minor_units >= 0 AND disputed_minor_units >= 0",
    )
    op.create_check_constraint(
        "ck_credit_purchase_money_bounded",
        "credit_purchases",
        "refunded_minor_units <= captured_minor_units "
        "AND disputed_minor_units <= captured_minor_units",
    )
    for column in (
        "intent_state",
        "request_hash",
        "catalog_version_id",
        "internal_metadata_id",
    ):
        op.create_index(f"ix_credit_purchases_{column}", "credit_purchases", [column])


def _expand_payment_events() -> None:
    columns = (
        sa.Column("raw_payload_sha256", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("customer_id", sa.String(128), nullable=True),
        sa.Column("pre_tax_minor_units", sa.Integer(), nullable=True),
        sa.Column("tax_minor_units", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("normalized_status", sa.String(64), nullable=True),
        sa.Column("business_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("processing_state", sa.String(32), nullable=True),
    )
    for column in columns:
        op.add_column("payment_events", column)
    op.execute(
        sa.text(
            "UPDATE payment_events SET processing_state = "
            "CASE WHEN processed_at IS NULL THEN 'RECEIVED' ELSE 'APPLIED' END"
        )
    )
    op.alter_column(
        "payment_events",
        "processing_state",
        nullable=False,
        server_default="RECEIVED",
    )
    op.create_check_constraint(
        "ck_payment_event_pre_tax_nonnegative",
        "payment_events",
        "pre_tax_minor_units IS NULL OR pre_tax_minor_units >= 0",
    )
    op.create_check_constraint(
        "ck_payment_event_tax_nonnegative",
        "payment_events",
        "tax_minor_units IS NULL OR tax_minor_units >= 0",
    )
    for column in (
        "raw_payload_sha256",
        "occurred_at",
        "request_id",
        "processing_state",
    ):
        op.create_index(f"ix_payment_events_{column}", "payment_events", [column])


def _create_payment_facts() -> None:
    op.create_table(
        "payment_capture_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_payment_id", sa.String(128), nullable=False),
        sa.Column("pre_tax_minor_units", sa.Integer(), nullable=False),
        sa.Column("tax_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("grant_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_id"], ["credit_purchases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grant_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["grant_lot_id"], ["credit_grant_lots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("purchase_id", name="uq_payment_capture_purchase"),
        sa.UniqueConstraint("payment_event_id", name="uq_payment_capture_event"),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment_capture_provider_payment"),
        sa.UniqueConstraint("grant_transaction_id", name="uq_payment_capture_grant_transaction"),
        sa.UniqueConstraint("grant_lot_id", name="uq_payment_capture_grant_lot"),
        sa.CheckConstraint("pre_tax_minor_units > 0 AND tax_minor_units >= 0", name="ck_payment_capture_amounts"),
    )
    op.create_index("ix_payment_capture_facts_purchase_id", "payment_capture_facts", ["purchase_id"])

    op.create_table(
        "payment_refund_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_refund_id", sa.String(128), nullable=False),
        sa.Column("refund_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("reversal_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_id"], ["credit_purchases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("payment_event_id", name="uq_payment_refund_event"),
        sa.UniqueConstraint("provider", "provider_refund_id", name="uq_payment_refund_provider_refund"),
        sa.CheckConstraint("refund_minor_units > 0", name="ck_payment_refund_amount_positive"),
    )
    op.create_index("ix_payment_refund_facts_purchase_id", "payment_refund_facts", ["purchase_id"])

    op.create_table(
        "payment_dispute_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_dispute_id", sa.String(128), nullable=False),
        sa.Column("disputed_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reversal_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_id"], ["credit_purchases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("payment_event_id", name="uq_payment_dispute_event"),
        sa.UniqueConstraint(
            "provider",
            "provider_dispute_id",
            "outcome",
            name="uq_payment_dispute_provider_outcome",
        ),
        sa.CheckConstraint("disputed_minor_units > 0", name="ck_payment_dispute_amount_positive"),
    )
    op.create_index("ix_payment_dispute_facts_purchase_id", "payment_dispute_facts", ["purchase_id"])


def _create_payment_transition_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_purchase_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_purchase_transition_guard$
            BEGIN
                IF OLD.user_id <> NEW.user_id
                   OR OLD.provider <> NEW.provider
                   OR OLD.package_id <> NEW.package_id
                   OR OLD.credits <> NEW.credits
                   OR OLD.price_cents <> NEW.price_cents
                   OR OLD.currency <> NEW.currency
                   OR OLD.provider_request_id <> NEW.provider_request_id
                   OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
                   OR OLD.catalog_version_id IS DISTINCT FROM NEW.catalog_version_id
                   OR OLD.catalog_snapshot IS DISTINCT FROM NEW.catalog_snapshot
                   OR OLD.internal_metadata_id IS DISTINCT FROM NEW.internal_metadata_id THEN
                    RAISE EXCEPTION 'purchase immutable field changed' USING ERRCODE = '23514';
                END IF;
                IF OLD.captured_minor_units > NEW.captured_minor_units
                   OR OLD.tax_minor_units > NEW.tax_minor_units
                   OR OLD.refunded_minor_units > NEW.refunded_minor_units
                   OR OLD.disputed_minor_units > NEW.disputed_minor_units THEN
                    RAISE EXCEPTION 'purchase money fact regressed' USING ERRCODE = '23514';
                END IF;
                IF OLD.intent_state <> NEW.intent_state AND NOT (
                    (OLD.intent_state = 'NEW' AND NEW.intent_state = 'CALLING') OR
                    (OLD.intent_state = 'CALLING' AND NEW.intent_state IN ('READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED')) OR
                    (OLD.intent_state = 'READY' AND NEW.intent_state = 'CONFIRMED') OR
                    (OLD.intent_state = 'UNKNOWN' AND NEW.intent_state IN ('READY','FAILED_RETRYABLE','CONFIRMED')) OR
                    (OLD.intent_state = 'FAILED_RETRYABLE' AND NEW.intent_state = 'NEW')
                ) THEN
                    RAISE EXCEPTION 'invalid purchase intent transition' USING ERRCODE = '23514';
                END IF;
                IF NEW.intent_state = 'CONFIRMED'
                   AND (NEW.captured_minor_units <= 0 OR NEW.grant_transaction_id IS NULL
                        OR NEW.grant_lot_id IS NULL OR NEW.confirmed_at IS NULL) THEN
                    RAISE EXCEPTION 'confirmed purchase lineage incomplete' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_purchase_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_credit_purchases_transition
            BEFORE UPDATE ON public.credit_purchases
            FOR EACH ROW EXECUTE FUNCTION public.commercial_purchase_transition_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_payment_event_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_payment_event_transition_guard$
            BEGIN
                IF OLD.provider <> NEW.provider OR OLD.event_id <> NEW.event_id
                   OR OLD.event_type <> NEW.event_type
                   OR OLD.object_id IS DISTINCT FROM NEW.object_id
                   OR OLD.payload_json IS DISTINCT FROM NEW.payload_json
                   OR OLD.raw_payload_sha256 IS DISTINCT FROM NEW.raw_payload_sha256
                   OR OLD.occurred_at IS DISTINCT FROM NEW.occurred_at
                   OR OLD.request_id IS DISTINCT FROM NEW.request_id
                   OR OLD.customer_id IS DISTINCT FROM NEW.customer_id
                   OR OLD.pre_tax_minor_units IS DISTINCT FROM NEW.pre_tax_minor_units
                   OR OLD.tax_minor_units IS DISTINCT FROM NEW.tax_minor_units
                   OR OLD.currency IS DISTINCT FROM NEW.currency
                   OR OLD.normalized_status IS DISTINCT FROM NEW.normalized_status
                   OR OLD.business_metadata IS DISTINCT FROM NEW.business_metadata THEN
                    RAISE EXCEPTION 'payment event immutable field changed' USING ERRCODE = '23514';
                END IF;
                IF OLD.processing_state <> NEW.processing_state AND NOT (
                    (OLD.processing_state = 'RECEIVED' AND NEW.processing_state IN ('UNHANDLED','APPLIED','RECONCILIATION_REQUIRED')) OR
                    (OLD.processing_state = 'RECONCILIATION_REQUIRED' AND NEW.processing_state = 'APPLIED')
                ) THEN
                    RAISE EXCEPTION 'invalid payment event transition' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_payment_event_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_payment_events_transition
            BEFORE UPDATE ON public.payment_events
            FOR EACH ROW EXECUTE FUNCTION public.commercial_payment_event_transition_guard();
            """
        )
    )


def _secure_payment_facts() -> None:
    for table in PAYMENT_FACT_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON public.{table} "
                "FOR EACH ROW EXECUTE FUNCTION public.commercial_append_only_guard()"
            )
        )
    for table in (*PAYMENT_FACT_TABLES, "payment_events"):
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))


def upgrade() -> None:
    _expand_purchase_intents()
    _expand_payment_events()
    _create_payment_facts()
    _create_payment_transition_guards()
    _secure_payment_facts()


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_events_transition ON public.payment_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_payment_event_transition_guard()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_purchases_transition ON public.credit_purchases"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.commercial_purchase_transition_guard()"))
    for table in reversed(PAYMENT_FACT_TABLES):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON public.{table}"))
        op.drop_table(table)
    for column in (
        "processing_state",
        "business_metadata",
        "normalized_status",
        "currency",
        "tax_minor_units",
        "pre_tax_minor_units",
        "customer_id",
        "request_id",
        "occurred_at",
        "raw_payload_sha256",
    ):
        index_name = f"ix_payment_events_{column}"
        if column in {"raw_payload_sha256", "occurred_at", "request_id", "processing_state"}:
            op.drop_index(index_name, table_name="payment_events")
        op.drop_column("payment_events", column)
    for column in (
        "internal_metadata_id",
        "catalog_version_id",
        "request_hash",
        "intent_state",
    ):
        op.drop_index(f"ix_credit_purchases_{column}", table_name="credit_purchases")
    op.drop_constraint("ck_credit_purchase_money_bounded", "credit_purchases", type_="check")
    op.drop_constraint("ck_credit_purchase_money_nonnegative", "credit_purchases", type_="check")
    op.drop_constraint("ck_credit_purchase_intent_state", "credit_purchases", type_="check")
    op.drop_constraint("uq_credit_purchase_grant_lot", "credit_purchases", type_="unique")
    op.drop_constraint("uq_credit_purchase_grant_transaction", "credit_purchases", type_="unique")
    op.drop_constraint("uq_credit_purchase_internal_metadata", "credit_purchases", type_="unique")
    op.drop_constraint("fk_credit_purchases_grant_lot", "credit_purchases", type_="foreignkey")
    op.drop_constraint("fk_credit_purchases_grant_transaction", "credit_purchases", type_="foreignkey")
    op.drop_constraint("fk_credit_purchases_catalog_version", "credit_purchases", type_="foreignkey")
    for column in (
        "confirmed_at",
        "ready_at",
        "call_started_at",
        "grant_lot_id",
        "grant_transaction_id",
        "dispute_state",
        "disputed_minor_units",
        "refunded_minor_units",
        "tax_minor_units",
        "captured_minor_units",
        "stored_response",
        "internal_metadata_id",
        "catalog_snapshot",
        "catalog_version_id",
        "request_hash",
        "intent_state",
    ):
        op.drop_column("credit_purchases", column)
