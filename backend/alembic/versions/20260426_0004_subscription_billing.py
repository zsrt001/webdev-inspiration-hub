"""Add subscription billing tables.

Revision ID: 20260426_0004
Revises: 20260426_0003
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260426_0004"
down_revision = "20260426_0003"
branch_labels = None
depends_on = None


PLAN_SEEDS = (
    {
        "id": "30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0001",
        "code": "starter_monthly",
        "name": "Starter Monthly",
        "billing_interval": "month",
        "price_cents": 1900,
        "currency": "USD",
        "monthly_credits": 80,
        "feature_flags": '{"remote_join": true, "live_portrait": false}',
    },
    {
        "id": "30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0002",
        "code": "creator_monthly",
        "name": "Creator Monthly",
        "billing_interval": "month",
        "price_cents": 4900,
        "currency": "USD",
        "monthly_credits": 300,
        "feature_flags": '{"remote_join": true, "live_portrait": true}',
    },
    {
        "id": "30aa1d5f-bbbd-4b1d-9f5b-2608ad6f0003",
        "code": "studio_monthly",
        "name": "Studio Monthly",
        "billing_interval": "month",
        "price_cents": 12900,
        "currency": "USD",
        "monthly_credits": 900,
        "feature_flags": '{"remote_join": true, "live_portrait": true, "priority_generation": true}',
    },
)


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _table_exists(table_name: str) -> bool:
    return table_name in _table_names()


def _constraint_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
        {"name": name},
    )
    return result.scalar_one_or_none() is not None


def _create_index(index_name: str, table_name: str, columns: str, unique: bool = False) -> None:
    unique_sql = "UNIQUE " if unique else ""
    op.execute(
        sa.text(
            f'CREATE {unique_sql}INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ({columns})'
        )
    )


def _add_unique_constraint_if_missing(table_name: str, constraint_name: str, columns: str) -> None:
    if _constraint_exists(constraint_name):
        return
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{constraint_name}" UNIQUE ({columns})'))


def _supabase_auth_available() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'auth' AND p.proname = 'uid'
            LIMIT 1
            """
        )
    )
    return result.scalar_one_or_none() is not None


def _enable_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"))


def _disable_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY"))


def _replace_select_policy(table_name: str, policy_name: str, predicate: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON public.{table_name}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {policy_name}
            ON public.{table_name}
            FOR SELECT
            TO authenticated
            USING ({predicate})
            """
        )
    )


def _drop_policy(table_name: str, policy_name: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON public.{table_name}"))


def _ensure_subscription_plans(existing_tables: set[str]) -> None:
    if "subscription_plans" not in existing_tables:
        op.create_table(
            "subscription_plans",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("billing_interval", sa.String(length=16), server_default="month", nullable=False),
            sa.Column("price_cents", sa.Integer(), server_default="0", nullable=False),
            sa.Column("currency", sa.String(length=8), server_default="USD", nullable=False),
            sa.Column("monthly_credits", sa.Integer(), server_default="0", nullable=False),
            sa.Column("feature_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("code", name="uq_subscription_plans_code"),
        )
    else:
        _add_unique_constraint_if_missing("subscription_plans", "uq_subscription_plans_code", "code")

    _create_index("ix_subscription_plans_code", "subscription_plans", "code")
    _create_index("ix_subscription_plans_billing_interval", "subscription_plans", "billing_interval")
    _create_index("ix_subscription_plans_is_active", "subscription_plans", "is_active")


def _ensure_user_subscriptions(existing_tables: set[str]) -> None:
    if "user_subscriptions" not in existing_tables:
        op.create_table(
            "user_subscriptions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("provider", sa.String(length=32), server_default="creem", nullable=False),
            sa.Column("provider_customer_id", sa.String(length=128), nullable=True),
            sa.Column("provider_subscription_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
            sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("provider", "provider_subscription_id", name="uq_user_subscriptions_provider_subscription_id"),
        )
    else:
        _add_unique_constraint_if_missing(
            "user_subscriptions",
            "uq_user_subscriptions_provider_subscription_id",
            "provider, provider_subscription_id",
        )

    _create_index("ix_user_subscriptions_user_id", "user_subscriptions", "user_id")
    _create_index("ix_user_subscriptions_plan_id", "user_subscriptions", "plan_id")
    _create_index("ix_user_subscriptions_provider", "user_subscriptions", "provider")
    _create_index("ix_user_subscriptions_provider_customer_id", "user_subscriptions", "provider_customer_id")
    _create_index("ix_user_subscriptions_provider_subscription_id", "user_subscriptions", "provider_subscription_id")
    _create_index("ix_user_subscriptions_status", "user_subscriptions", "status")


def _ensure_subscription_credit_grants(existing_tables: set[str]) -> None:
    if "subscription_credit_grants" not in existing_tables:
        op.create_table(
            "subscription_credit_grants",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("period_key", sa.String(length=32), nullable=False),
            sa.Column("credits", sa.Integer(), nullable=False),
            sa.Column("credit_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["credit_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["subscription_id"], ["user_subscriptions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("subscription_id", "period_key", name="uq_subscription_credit_grant_period"),
        )
    else:
        _add_unique_constraint_if_missing(
            "subscription_credit_grants",
            "uq_subscription_credit_grant_period",
            "subscription_id, period_key",
        )

    _create_index("ix_subscription_credit_grants_subscription_id", "subscription_credit_grants", "subscription_id")
    _create_index("ix_subscription_credit_grants_user_id", "subscription_credit_grants", "user_id")
    _create_index("ix_subscription_credit_grants_period_key", "subscription_credit_grants", "period_key")
    _create_index("ix_subscription_credit_grants_credit_transaction_id", "subscription_credit_grants", "credit_transaction_id")


def _ensure_payment_events(existing_tables: set[str]) -> None:
    if "payment_events" not in existing_tables:
        op.create_table(
            "payment_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("provider", sa.String(length=32), server_default="creem", nullable=False),
            sa.Column("event_id", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=128), nullable=False),
            sa.Column("object_id", sa.String(length=128), nullable=True),
            sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider", "event_id", name="uq_payment_events_provider_event_id"),
        )
    else:
        _add_unique_constraint_if_missing(
            "payment_events",
            "uq_payment_events_provider_event_id",
            "provider, event_id",
        )

    _create_index("ix_payment_events_provider", "payment_events", "provider")
    _create_index("ix_payment_events_event_id", "payment_events", "event_id")
    _create_index("ix_payment_events_event_type", "payment_events", "event_type")
    _create_index("ix_payment_events_object_id", "payment_events", "object_id")


def _seed_subscription_plans() -> None:
    bind = op.get_bind()
    statement = sa.text(
        """
        INSERT INTO subscription_plans (
            id, code, name, billing_interval, price_cents, currency,
            monthly_credits, feature_flags, is_active
        )
        VALUES (
            :id, :code, :name, :billing_interval, :price_cents, :currency,
            :monthly_credits, CAST(:feature_flags AS jsonb), true
        )
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            billing_interval = EXCLUDED.billing_interval,
            price_cents = EXCLUDED.price_cents,
            currency = EXCLUDED.currency,
            monthly_credits = EXCLUDED.monthly_credits,
            feature_flags = EXCLUDED.feature_flags,
            is_active = true,
            updated_at = now()
        """
    )
    for plan in PLAN_SEEDS:
        bind.execute(statement, plan)


def _apply_rls() -> None:
    if not _supabase_auth_available():
        return

    _enable_rls("subscription_plans")
    _replace_select_policy("subscription_plans", "subscription_plans_select_active", "is_active = true")

    for table_name in ("user_subscriptions", "subscription_credit_grants"):
        _enable_rls(table_name)
        _replace_select_policy(table_name, f"{table_name}_select_own", "user_id = public.app_current_user_id()")

    # Provider webhook events are service-only; no authenticated direct-read policy.
    _enable_rls("payment_events")


def upgrade() -> None:
    existing_tables = _table_names()
    _ensure_subscription_plans(existing_tables)
    existing_tables = _table_names()
    _ensure_user_subscriptions(existing_tables)
    _ensure_subscription_credit_grants(existing_tables)
    _ensure_payment_events(existing_tables)
    _seed_subscription_plans()
    _apply_rls()


def downgrade() -> None:
    if _supabase_auth_available():
        if _table_exists("subscription_plans"):
            _drop_policy("subscription_plans", "subscription_plans_select_active")
            _disable_rls("subscription_plans")
        for table_name in ("user_subscriptions", "subscription_credit_grants"):
            if _table_exists(table_name):
                _drop_policy(table_name, f"{table_name}_select_own")
                _disable_rls(table_name)
        if _table_exists("payment_events"):
            _disable_rls("payment_events")

    for table_name in (
        "payment_events",
        "subscription_credit_grants",
        "user_subscriptions",
        "subscription_plans",
    ):
        if _table_exists(table_name):
            op.drop_table(table_name)
