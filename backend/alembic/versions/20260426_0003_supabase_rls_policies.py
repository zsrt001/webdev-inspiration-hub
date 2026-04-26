"""Add Supabase row-level security policies for SaaS data.

Revision ID: 20260426_0003
Revises: 20260426_0002
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260426_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


USER_OWNED_TABLES = (
    "users",
    "user_credits",
    "credit_transactions",
    "credit_purchases",
    "orders",
    "live_portrait_jobs",
)

SERVICE_ONLY_TABLES = (
    "leads",
    "click_stats",
)

# Protected public tables: public.users, public.user_credits,
# public.credit_transactions, public.credit_purchases, public.orders,
# public.live_portrait_jobs, public.leads, public.click_stats.

FUTURE_USER_ID_TABLES = (
    "payment_customers",
    "creem_customers",
    "subscriptions",
    "customer_subscriptions",
    "referral_codes",
    "user_referrals",
)

FUTURE_RELATION_TABLES = {
    "referrals": ("referrer_user_id", "referred_user_id"),
    "invites": ("inviter_user_id", "invitee_user_id"),
}


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names(schema="public"))


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name, schema="public")}


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


def upgrade() -> None:
    # This migration is intentionally Supabase-specific. Local Postgres does not
    # provide auth.uid(), so skip there instead of breaking local development.
    if not _supabase_auth_available():
        return

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.app_current_user_id()
            RETURNS uuid
            LANGUAGE sql
            STABLE
            SECURITY INVOKER
            SET search_path = public, auth
            AS $$
                SELECT users.id
                FROM public.users
                WHERE (
                    users.auth_provider = 'supabase'
                    AND users.auth_subject = (select auth.uid())::text
                )
                OR users.id = (select auth.uid())
                LIMIT 1
            $$;
            """
        )
    )

    for table_name in USER_OWNED_TABLES:
        if not _table_exists(table_name):
            continue
        _enable_rls(table_name)
        predicate = "id = public.app_current_user_id()" if table_name == "users" else "user_id = public.app_current_user_id()"
        _replace_select_policy(table_name, f"{table_name}_select_own", predicate)

    # Contact leads and aggregate analytics are service-only in this app. Enabling
    # RLS with no authenticated policies denies direct Supabase client reads.
    for table_name in SERVICE_ONLY_TABLES:
        if _table_exists(table_name):
            _enable_rls(table_name)

    # Future commercial tables: Creem customer/subscription state and referral
    # ownership should follow the same per-user visibility model if those tables
    # already exist in a deployed database.
    for table_name in FUTURE_USER_ID_TABLES:
        if not _table_exists(table_name) or "user_id" not in _columns(table_name):
            continue
        _enable_rls(table_name)
        _replace_select_policy(table_name, f"{table_name}_select_own", "user_id = public.app_current_user_id()")

    for table_name, user_columns in FUTURE_RELATION_TABLES.items():
        existing_columns = _columns(table_name)
        available_columns = [column for column in user_columns if column in existing_columns]
        if not available_columns:
            continue
        _enable_rls(table_name)
        predicate = " OR ".join(f"{column} = public.app_current_user_id()" for column in available_columns)
        _replace_select_policy(table_name, f"{table_name}_select_related_user", predicate)


def downgrade() -> None:
    for table_name in USER_OWNED_TABLES:
        if not _table_exists(table_name):
            continue
        _drop_policy(table_name, f"{table_name}_select_own")
        _disable_rls(table_name)

    for table_name in SERVICE_ONLY_TABLES:
        if _table_exists(table_name):
            _disable_rls(table_name)

    for table_name in FUTURE_USER_ID_TABLES:
        if not _table_exists(table_name):
            continue
        _drop_policy(table_name, f"{table_name}_select_own")
        _disable_rls(table_name)

    for table_name in FUTURE_RELATION_TABLES:
        if not _table_exists(table_name):
            continue
        _drop_policy(table_name, f"{table_name}_select_related_user")
        _disable_rls(table_name)

    op.execute(sa.text("DROP FUNCTION IF EXISTS public.app_current_user_id()"))
