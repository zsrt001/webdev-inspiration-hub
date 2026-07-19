"""Normalize subscription invoices, grants, adjustments and cancellation.

Revision ID: 20260710_0018
Revises: 20260710_0017
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0018"
down_revision = "20260710_0017"
branch_labels = None
depends_on = None


SUBSCRIPTION_FACT_TABLES = (
    "subscription_checkout_intents",
    "subscription_invoices",
    "subscription_invoice_adjustment_facts",
    "subscription_cancel_intents",
)


def _expand_subscription_projection() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("catalog_product_code", sa.String(64), nullable=True),
    )
    op.execute(sa.text("UPDATE subscription_plans SET catalog_product_code = code"))
    op.create_index(
        "ix_subscription_plans_catalog_product_code",
        "subscription_plans",
        ["catalog_product_code"],
    )

    op.add_column("user_subscriptions", sa.Column("normalized_status", sa.String(32), nullable=True))
    op.add_column(
        "user_subscriptions",
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("user_subscriptions", sa.Column("product_code", sa.String(64), nullable=True))
    op.add_column(
        "user_subscriptions",
        sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "user_subscriptions",
        sa.Column("paid_through_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_subscriptions",
        sa.Column("last_provider_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_subscriptions",
        sa.Column("last_provider_transaction_id", sa.String(128), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE user_subscriptions SET normalized_status = CASE "
            "WHEN status = 'active' THEN 'ACTIVE' "
            "WHEN status = 'past_due' THEN 'PAST_DUE' "
            "WHEN status = 'canceled' THEN 'CANCELED' "
            "WHEN status = 'expired' THEN 'EXPIRED' "
            "ELSE 'PENDING' END"
        )
    )
    op.alter_column(
        "user_subscriptions",
        "normalized_status",
        nullable=False,
        server_default="PENDING",
    )
    op.create_foreign_key(
        "fk_user_subscriptions_catalog_version",
        "user_subscriptions",
        "billing_catalog_versions",
        ["catalog_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_user_subscriptions_normalized_status",
        "user_subscriptions",
        "normalized_status IN ('PENDING','ACTIVE','PAST_DUE','CANCEL_REQUESTED','CANCELED','EXPIRED')",
    )
    op.create_index(
        "uq_user_subscriptions_one_nonterminal",
        "user_subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "normalized_status IN ('PENDING','ACTIVE','PAST_DUE','CANCEL_REQUESTED')"
        ),
    )
    for column in ("normalized_status", "catalog_version_id", "product_code"):
        op.create_index(f"ix_user_subscriptions_{column}", "user_subscriptions", [column])


def _create_invoices() -> None:
    op.create_table(
        "subscription_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128), nullable=False),
        sa.Column("provider_invoice_id", sa.String(128), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pre_tax_minor_units", sa.Integer(), nullable=False),
        sa.Column("tax_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_status", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_sha256", sa.String(64), nullable=False),
        sa.Column("catalog_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("credit_grant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("refunded_minor_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disputed_minor_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dispute_state", sa.String(16), server_default="NONE", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["user_subscriptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["billing_catalog_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "provider",
            "provider_transaction_id",
            name="uq_subscription_invoice_provider_transaction",
        ),
        sa.UniqueConstraint(
            "subscription_id",
            "period_start",
            "period_end",
            name="uq_subscription_invoice_period",
        ),
        sa.UniqueConstraint("payment_event_id", name="uq_subscription_invoice_payment_event"),
        sa.UniqueConstraint("credit_grant_id", name="uq_subscription_invoice_credit_grant"),
        sa.CheckConstraint("period_end > period_start", name="ck_subscription_invoice_period"),
        sa.CheckConstraint(
            "pre_tax_minor_units > 0 AND tax_minor_units >= 0",
            name="ck_subscription_invoice_amounts",
        ),
        sa.CheckConstraint(
            "refunded_minor_units >= 0 AND disputed_minor_units >= 0 "
            "AND refunded_minor_units <= pre_tax_minor_units + tax_minor_units "
            "AND disputed_minor_units <= pre_tax_minor_units + tax_minor_units",
            name="ck_subscription_invoice_adjustment_bounds",
        ),
    )
    for column in (
        "subscription_id",
        "user_id",
        "provider_transaction_id",
        "provider_invoice_id",
        "period_start",
        "period_end",
    ):
        op.create_index(f"ix_subscription_invoices_{column}", "subscription_invoices", [column])


def _create_checkout_intents() -> None:
    op.create_table(
        "subscription_checkout_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "catalog_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=False),
        sa.Column("internal_metadata_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), server_default="NEW", nullable=False),
        sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("stored_response", postgresql.JSONB(), nullable=True),
        sa.Column("provider_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("provider_checkout_id", sa.String(128), nullable=True),
        sa.Column("provider_subscription_id", sa.String(128), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("call_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_version_id"],
            ["billing_catalog_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_subscription_checkout_user_idempotency",
        ),
        sa.UniqueConstraint(
            "provider_request_id",
            name="uq_subscription_checkout_provider_request",
        ),
        sa.UniqueConstraint(
            "internal_metadata_id",
            name="uq_subscription_checkout_internal_metadata",
        ),
        sa.UniqueConstraint(
            "provider_checkout_id",
            name="uq_subscription_checkout_provider_checkout",
        ),
        sa.UniqueConstraint(
            "provider_subscription_id",
            name="uq_subscription_checkout_provider_subscription",
        ),
        sa.CheckConstraint(
            "state IN ('NEW','CALLING','READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED')",
            name="ck_subscription_checkout_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_subscription_checkout_attempts",
        ),
    )
    op.create_index(
        "ix_subscription_checkout_intents_user_id",
        "subscription_checkout_intents",
        ["user_id"],
    )
    op.create_index(
        "ix_subscription_checkout_intents_plan_id",
        "subscription_checkout_intents",
        ["plan_id"],
    )
    op.create_index(
        "ix_subscription_checkout_intents_catalog_version_id",
        "subscription_checkout_intents",
        ["catalog_version_id"],
    )
    op.create_index(
        "ix_subscription_checkout_intents_state",
        "subscription_checkout_intents",
        ["state"],
    )
    op.create_index(
        "ix_subscription_checkout_intents_provider_checkout_id",
        "subscription_checkout_intents",
        ["provider_checkout_id"],
    )
    op.create_index(
        "ix_subscription_checkout_intents_provider_subscription_id",
        "subscription_checkout_intents",
        ["provider_subscription_id"],
    )
    op.create_index(
        "uq_subscription_checkout_one_nonterminal",
        "subscription_checkout_intents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('NEW','CALLING','READY','UNKNOWN','FAILED_RETRYABLE')"
        ),
    )


def _expand_subscription_grants() -> None:
    op.drop_constraint(
        "subscription_credit_grants_subscription_id_fkey",
        "subscription_credit_grants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_subscription_grants_subscription",
        "subscription_credit_grants",
        "user_subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "subscription_credit_grants",
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscription_credit_grants",
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscription_credit_grants",
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "subscription_credit_grants",
        sa.Column("grant_lot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_subscription_grants_invoice",
        "subscription_credit_grants",
        "subscription_invoices",
        ["invoice_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_subscription_grants_lot",
        "subscription_credit_grants",
        "credit_grant_lots",
        ["grant_lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_subscription_credit_grant_invoice",
        "subscription_credit_grants",
        ["invoice_id"],
    )
    op.create_unique_constraint(
        "uq_subscription_credit_grant_lot",
        "subscription_credit_grants",
        ["grant_lot_id"],
    )
    op.create_unique_constraint(
        "uq_subscription_credit_grant_exact_period",
        "subscription_credit_grants",
        ["subscription_id", "period_start", "period_end"],
    )
    op.create_index(
        "ix_subscription_credit_grants_invoice_id",
        "subscription_credit_grants",
        ["invoice_id"],
    )
    op.create_index(
        "ix_subscription_credit_grants_grant_lot_id",
        "subscription_credit_grants",
        ["grant_lot_id"],
    )
    op.create_foreign_key(
        "fk_subscription_invoices_credit_grant",
        "subscription_invoices",
        "subscription_credit_grants",
        ["credit_grant_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_adjustments_and_cancellation() -> None:
    op.create_table(
        "subscription_invoice_adjustment_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_object_id", sa.String(128), nullable=False),
        sa.Column("adjustment_kind", sa.String(16), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reversal_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_event_id"], ["payment_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("payment_event_id", name="uq_subscription_adjustment_event"),
        sa.UniqueConstraint(
            "provider",
            "provider_object_id",
            "adjustment_kind",
            "outcome",
            name="uq_subscription_adjustment_provider_outcome",
        ),
        sa.CheckConstraint("amount_minor_units > 0", name="ck_subscription_adjustment_amount"),
        sa.CheckConstraint(
            "adjustment_kind IN ('REFUND','DISPUTE')",
            name="ck_subscription_adjustment_kind",
        ),
    )
    op.create_index(
        "ix_subscription_invoice_adjustment_facts_invoice_id",
        "subscription_invoice_adjustment_facts",
        ["invoice_id"],
    )

    op.create_table(
        "subscription_cancel_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), server_default="NEW", nullable=False),
        sa.Column("stored_response", postgresql.JSONB(), nullable=True),
        sa.Column("provider_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["user_subscriptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_subscription_cancel_user_idempotency",
        ),
        sa.UniqueConstraint(
            "provider_request_id",
            name="uq_subscription_cancel_provider_request",
        ),
        sa.CheckConstraint(
            "state IN ('NEW','CALLING','UNKNOWN','CONFIRMED','FAILED_RETRYABLE')",
            name="ck_subscription_cancel_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_subscription_cancel_attempts"),
    )
    op.create_index(
        "ix_subscription_cancel_intents_subscription_id",
        "subscription_cancel_intents",
        ["subscription_id"],
    )
    op.create_index(
        "ix_subscription_cancel_intents_user_id",
        "subscription_cancel_intents",
        ["user_id"],
    )


def _create_subscription_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.subscription_checkout_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $subscription_checkout_transition_guard$
            BEGIN
                IF OLD.user_id <> NEW.user_id OR OLD.plan_id <> NEW.plan_id
                   OR OLD.catalog_version_id <> NEW.catalog_version_id
                   OR OLD.product_code <> NEW.product_code
                   OR OLD.idempotency_key <> NEW.idempotency_key
                   OR OLD.request_hash <> NEW.request_hash
                   OR OLD.provider_request_id <> NEW.provider_request_id
                   OR OLD.internal_metadata_id <> NEW.internal_metadata_id
                   OR OLD.catalog_snapshot <> NEW.catalog_snapshot THEN
                    RAISE EXCEPTION 'subscription checkout immutable field changed'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.provider_checkout_id IS NOT NULL
                   AND OLD.provider_checkout_id IS DISTINCT FROM NEW.provider_checkout_id THEN
                    RAISE EXCEPTION 'subscription checkout Provider ID changed'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.provider_subscription_id IS NOT NULL
                   AND OLD.provider_subscription_id IS DISTINCT FROM NEW.provider_subscription_id THEN
                    RAISE EXCEPTION 'subscription checkout subscription ID changed'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.state <> NEW.state AND NOT (
                    (OLD.state = 'NEW' AND NEW.state = 'CALLING') OR
                    (OLD.state = 'CALLING' AND NEW.state IN (
                        'READY','UNKNOWN','FAILED_RETRYABLE','CONFIRMED'
                    )) OR
                    (OLD.state = 'READY' AND NEW.state = 'CONFIRMED') OR
                    (OLD.state = 'UNKNOWN' AND NEW.state IN (
                        'READY','FAILED_RETRYABLE','CONFIRMED'
                    )) OR
                    (OLD.state = 'FAILED_RETRYABLE' AND NEW.state IN (
                        'NEW','CONFIRMED'
                    ))
                ) THEN
                    RAISE EXCEPTION 'invalid subscription checkout transition'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.state = 'READY'
                   AND (
                       NEW.provider_checkout_id IS NULL
                       OR NEW.checkout_url IS NULL
                       OR NEW.stored_response IS NULL
                       OR NEW.ready_at IS NULL
                   ) THEN
                    RAISE EXCEPTION 'ready subscription checkout evidence missing'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.state = 'CONFIRMED'
                   AND (
                       NEW.provider_subscription_id IS NULL
                       OR NEW.provider_evidence IS NULL
                       OR NEW.confirmed_at IS NULL
                   ) THEN
                    RAISE EXCEPTION 'confirmed subscription checkout evidence missing'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $subscription_checkout_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_subscription_checkout_intents_transition
            BEFORE UPDATE ON public.subscription_checkout_intents
            FOR EACH ROW EXECUTE FUNCTION public.subscription_checkout_transition_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.subscription_invoice_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $subscription_invoice_transition_guard$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'subscription invoice is append only' USING ERRCODE = '23514';
                END IF;
                IF OLD.subscription_id <> NEW.subscription_id OR OLD.user_id <> NEW.user_id
                   OR OLD.payment_event_id <> NEW.payment_event_id OR OLD.provider <> NEW.provider
                   OR OLD.provider_transaction_id <> NEW.provider_transaction_id
                   OR OLD.provider_invoice_id IS DISTINCT FROM NEW.provider_invoice_id
                   OR OLD.period_start <> NEW.period_start OR OLD.period_end <> NEW.period_end
                   OR OLD.pre_tax_minor_units <> NEW.pre_tax_minor_units
                   OR OLD.tax_minor_units <> NEW.tax_minor_units OR OLD.currency <> NEW.currency
                   OR OLD.provider_status <> NEW.provider_status OR OLD.occurred_at <> NEW.occurred_at
                   OR OLD.raw_payload_sha256 <> NEW.raw_payload_sha256
                   OR OLD.catalog_version_id <> NEW.catalog_version_id
                   OR OLD.catalog_snapshot <> NEW.catalog_snapshot THEN
                    RAISE EXCEPTION 'subscription invoice immutable field changed' USING ERRCODE = '23514';
                END IF;
                IF OLD.credit_grant_id IS NOT NULL AND OLD.credit_grant_id IS DISTINCT FROM NEW.credit_grant_id THEN
                    RAISE EXCEPTION 'subscription invoice grant changed' USING ERRCODE = '23514';
                END IF;
                IF NEW.refunded_minor_units < OLD.refunded_minor_units
                   OR NEW.disputed_minor_units < OLD.disputed_minor_units THEN
                    RAISE EXCEPTION 'subscription invoice money fact regressed' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $subscription_invoice_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_subscription_invoices_transition
            BEFORE UPDATE OR DELETE ON public.subscription_invoices
            FOR EACH ROW EXECUTE FUNCTION public.subscription_invoice_transition_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.subscription_grant_normalized_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $subscription_grant_normalized_guard$
            BEGIN
                IF TG_OP = 'DELETE' AND OLD.invoice_id IS NOT NULL THEN
                    RAISE EXCEPTION 'normalized subscription grant is append only' USING ERRCODE = '23514';
                ELSIF TG_OP = 'UPDATE' AND OLD.invoice_id IS NOT NULL THEN
                    RAISE EXCEPTION 'normalized subscription grant is append only' USING ERRCODE = '23514';
                END IF;
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END;
            $subscription_grant_normalized_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_subscription_credit_grants_normalized
            BEFORE UPDATE OR DELETE ON public.subscription_credit_grants
            FOR EACH ROW EXECUTE FUNCTION public.subscription_grant_normalized_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.subscription_cancel_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $subscription_cancel_transition_guard$
            BEGIN
                IF OLD.subscription_id <> NEW.subscription_id OR OLD.user_id <> NEW.user_id
                   OR OLD.idempotency_key <> NEW.idempotency_key
                   OR OLD.request_hash <> NEW.request_hash
                   OR OLD.provider_request_id <> NEW.provider_request_id THEN
                    RAISE EXCEPTION 'subscription cancel immutable field changed' USING ERRCODE = '23514';
                END IF;
                IF OLD.state <> NEW.state AND NOT (
                    (OLD.state = 'NEW' AND NEW.state = 'CALLING') OR
                    (OLD.state = 'CALLING' AND NEW.state IN ('UNKNOWN','CONFIRMED','FAILED_RETRYABLE')) OR
                    (OLD.state = 'UNKNOWN' AND NEW.state IN ('CONFIRMED','FAILED_RETRYABLE')) OR
                    (OLD.state = 'FAILED_RETRYABLE' AND NEW.state = 'NEW')
                ) THEN
                    RAISE EXCEPTION 'invalid subscription cancel transition' USING ERRCODE = '23514';
                END IF;
                IF NEW.state = 'CONFIRMED'
                   AND (NEW.stored_response IS NULL OR NEW.provider_evidence IS NULL
                        OR NEW.confirmed_at IS NULL) THEN
                    RAISE EXCEPTION 'confirmed subscription cancel evidence missing' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $subscription_cancel_transition_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_subscription_cancel_intents_transition
            BEFORE UPDATE ON public.subscription_cancel_intents
            FOR EACH ROW EXECUTE FUNCTION public.subscription_cancel_transition_guard();
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_subscription_invoice_adjustment_facts_append_only "
            "BEFORE UPDATE OR DELETE ON public.subscription_invoice_adjustment_facts "
            "FOR EACH ROW EXECUTE FUNCTION public.commercial_append_only_guard()"
        )
    )


def _secure_subscription_facts() -> None:
    for table in SUBSCRIPTION_FACT_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))


def upgrade() -> None:
    _expand_subscription_projection()
    _create_invoices()
    _create_checkout_intents()
    _expand_subscription_grants()
    _create_adjustments_and_cancellation()
    _create_subscription_guards()
    _secure_subscription_facts()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_subscription_invoice_adjustment_facts_append_only "
            "ON public.subscription_invoice_adjustment_facts"
        )
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_subscription_cancel_intents_transition ON public.subscription_cancel_intents")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.subscription_cancel_transition_guard()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_subscription_checkout_intents_transition "
            "ON public.subscription_checkout_intents"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.subscription_checkout_transition_guard()"
        )
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_subscription_credit_grants_normalized ON public.subscription_credit_grants")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.subscription_grant_normalized_guard()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_subscription_invoices_transition ON public.subscription_invoices"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.subscription_invoice_transition_guard()"))
    op.drop_table("subscription_cancel_intents")
    op.drop_table("subscription_checkout_intents")
    op.drop_table("subscription_invoice_adjustment_facts")
    op.drop_constraint("fk_subscription_invoices_credit_grant", "subscription_invoices", type_="foreignkey")
    op.drop_index("ix_subscription_credit_grants_grant_lot_id", table_name="subscription_credit_grants")
    op.drop_index("ix_subscription_credit_grants_invoice_id", table_name="subscription_credit_grants")
    op.drop_constraint("uq_subscription_credit_grant_exact_period", "subscription_credit_grants", type_="unique")
    op.drop_constraint("uq_subscription_credit_grant_lot", "subscription_credit_grants", type_="unique")
    op.drop_constraint("uq_subscription_credit_grant_invoice", "subscription_credit_grants", type_="unique")
    op.drop_constraint("fk_subscription_grants_lot", "subscription_credit_grants", type_="foreignkey")
    op.drop_constraint("fk_subscription_grants_invoice", "subscription_credit_grants", type_="foreignkey")
    op.drop_constraint("fk_subscription_grants_subscription", "subscription_credit_grants", type_="foreignkey")
    op.create_foreign_key(
        "subscription_credit_grants_subscription_id_fkey",
        "subscription_credit_grants",
        "user_subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for column in ("grant_lot_id", "invoice_id", "period_end", "period_start"):
        op.drop_column("subscription_credit_grants", column)
    op.drop_table("subscription_invoices")
    op.drop_index("uq_user_subscriptions_one_nonterminal", table_name="user_subscriptions")
    for column in ("product_code", "catalog_version_id", "normalized_status"):
        op.drop_index(f"ix_user_subscriptions_{column}", table_name="user_subscriptions")
    op.drop_constraint("ck_user_subscriptions_normalized_status", "user_subscriptions", type_="check")
    op.drop_constraint("fk_user_subscriptions_catalog_version", "user_subscriptions", type_="foreignkey")
    for column in (
        "last_provider_transaction_id",
        "last_provider_event_at",
        "paid_through_at",
        "catalog_snapshot",
        "product_code",
        "catalog_version_id",
        "normalized_status",
    ):
        op.drop_column("user_subscriptions", column)
    op.drop_index("ix_subscription_plans_catalog_product_code", table_name="subscription_plans")
    op.drop_column("subscription_plans", "catalog_product_code")
