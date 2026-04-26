"""Initial SaaS schema baseline.

Revision ID: 20260425_0001
Revises:
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260425_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _constraint_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
        {"name": name},
    )
    return result.scalar_one_or_none() is not None


def _add_unique_constraint_if_missing(table_name: str, constraint_name: str, columns: str) -> None:
    if _constraint_exists(constraint_name):
        return
    op.execute(
        sa.text(
            f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{constraint_name}" UNIQUE ({columns})'
        )
    )


def _create_index(index_name: str, table_name: str, columns: str, unique: bool = False) -> None:
    unique_sql = "UNIQUE " if unique else ""
    op.execute(
        sa.text(
            f'CREATE {unique_sql}INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ({columns})'
        )
    )


def _ensure_users(existing_tables: set[str]) -> None:
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("openid", sa.String(length=64), nullable=False),
            sa.Column("auth_provider", sa.String(length=32), nullable=True),
            sa.Column("auth_subject", sa.String(length=128), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("unionid", sa.String(length=64), nullable=True),
            sa.Column("nickname", sa.String(length=64), nullable=True),
            sa.Column("avatar_url", sa.String(length=512), nullable=True),
            sa.Column("role", sa.String(length=32), server_default="user", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("openid", name="uq_users_openid"),
            sa.UniqueConstraint("unionid", name="uq_users_unionid"),
            sa.UniqueConstraint("auth_provider", "auth_subject", name="uq_users_auth_provider_subject"),
        )
    else:
        cols = _columns("users")
        if "auth_provider" not in cols:
            op.add_column("users", sa.Column("auth_provider", sa.String(length=32), nullable=True))
        if "auth_subject" not in cols:
            op.add_column("users", sa.Column("auth_subject", sa.String(length=128), nullable=True))
        if "email" not in cols:
            op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
        if "role" not in cols:
            op.add_column("users", sa.Column("role", sa.String(length=32), server_default="user", nullable=False))
        if "status" not in cols:
            op.add_column("users", sa.Column("status", sa.String(length=32), server_default="active", nullable=False))
        if "last_login_at" not in cols:
            op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        _add_unique_constraint_if_missing(
            "users",
            "uq_users_auth_provider_subject",
            "auth_provider, auth_subject",
        )

    _create_index("ix_users_openid", "users", "openid")
    _create_index("ix_users_auth_provider", "users", "auth_provider")
    _create_index("ix_users_auth_subject", "users", "auth_subject")
    _create_index("ix_users_email", "users", "email")
    _create_index("ix_users_role", "users", "role")
    _create_index("ix_users_status", "users", "status")


def _ensure_orders(existing_tables: set[str]) -> None:
    if "orders" not in existing_tables:
        op.create_table(
            "orders",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("template_id", sa.String(length=64), nullable=True),
            sa.Column("style_template", sa.String(length=64), nullable=True),
            sa.Column("generation_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("source_image_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("preview_image_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("final_image_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("price_cents", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.String(length=128), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
    _create_index("ix_orders_user_id", "orders", "user_id")
    _create_index("ix_orders_status", "orders", "status")


def _ensure_live_portrait_jobs(existing_tables: set[str]) -> None:
    if "live_portrait_jobs" not in existing_tables:
        op.create_table(
            "live_portrait_jobs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source_image_url", sa.String(length=1024), nullable=False),
            sa.Column("seconds", sa.Integer(), nullable=False),
            sa.Column("video_url", sa.String(length=1024), nullable=True),
            sa.Column("generation_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("credits_cost", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
    _create_index("ix_live_portrait_jobs_user_id", "live_portrait_jobs", "user_id")
    _create_index("ix_live_portrait_jobs_status", "live_portrait_jobs", "status")


def _ensure_user_credits(existing_tables: set[str]) -> None:
    if "user_credits" not in existing_tables:
        op.create_table(
            "user_credits",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("balance", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", name="uq_user_credits_user_id"),
        )
    _create_index("ix_user_credits_user_id", "user_credits", "user_id")


def _ensure_credit_transactions(existing_tables: set[str]) -> None:
    if "credit_transactions" not in existing_tables:
        op.create_table(
            "credit_transactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("transaction_type", sa.String(length=32), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("balance_after", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("source_id", sa.String(length=128), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
    _create_index("ix_credit_transactions_user_id", "credit_transactions", "user_id")
    _create_index("ix_credit_transactions_transaction_type", "credit_transactions", "transaction_type")
    _create_index("ix_credit_transactions_source", "credit_transactions", "source")
    _create_index("ix_credit_transactions_source_id", "credit_transactions", "source_id")
    _create_index("ix_credit_transactions_created_at", "credit_transactions", "created_at")


def _ensure_credit_purchases(existing_tables: set[str]) -> None:
    if "credit_purchases" not in existing_tables:
        op.create_table(
            "credit_purchases",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("package_id", sa.String(length=64), nullable=False),
            sa.Column("credits", sa.Integer(), nullable=False),
            sa.Column("price_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("provider_request_id", sa.String(length=128), nullable=False),
            sa.Column("provider_checkout_id", sa.String(length=128), nullable=True),
            sa.Column("provider_payment_id", sa.String(length=128), nullable=True),
            sa.Column("checkout_url", sa.Text(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("webhook_event_id", sa.String(length=128), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("provider_request_id", name="uq_credit_purchase_request_id"),
            sa.UniqueConstraint("webhook_event_id", name="uq_credit_purchase_webhook_event_id"),
        )
    _add_unique_constraint_if_missing(
        "credit_purchases",
        "uq_credit_purchase_webhook_event_id",
        "webhook_event_id",
    )
    _create_index("ix_credit_purchases_user_id", "credit_purchases", "user_id")
    _create_index("ix_credit_purchases_provider", "credit_purchases", "provider")
    _create_index("ix_credit_purchases_package_id", "credit_purchases", "package_id")
    _create_index("ix_credit_purchases_status", "credit_purchases", "status")
    _create_index("ix_credit_purchases_provider_request_id", "credit_purchases", "provider_request_id")
    _create_index("ix_credit_purchases_provider_checkout_id", "credit_purchases", "provider_checkout_id")
    _create_index("ix_credit_purchases_webhook_event_id", "credit_purchases", "webhook_event_id")


def _ensure_leads(existing_tables: set[str]) -> None:
    if "leads" not in existing_tables:
        op.create_table(
            "leads",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("phone", sa.Text(), nullable=False),
            sa.Column("city", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
        )
    else:
        cols = _columns("leads")
        if "phone" in cols:
            op.execute(sa.text('ALTER TABLE "leads" ALTER COLUMN "phone" TYPE TEXT'))


def _ensure_click_stats(existing_tables: set[str]) -> None:
    if "click_stats" not in existing_tables:
        op.create_table(
            "click_stats",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("source_page", sa.String(length=80), nullable=False),
            sa.Column("template_id", sa.String(length=64), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("day", "event_type", "source_page", "template_id", name="uq_click_stat"),
        )
    _create_index("ix_click_stats_day", "click_stats", "day")
    _create_index("ix_click_stats_event_type", "click_stats", "event_type")
    _create_index("ix_click_stats_source_page", "click_stats", "source_page")
    _create_index("ix_click_stats_template_id", "click_stats", "template_id")


def upgrade() -> None:
    existing_tables = _table_names()
    _ensure_users(existing_tables)
    existing_tables = _table_names()
    _ensure_orders(existing_tables)
    _ensure_live_portrait_jobs(existing_tables)
    _ensure_user_credits(existing_tables)
    _ensure_credit_transactions(existing_tables)
    _ensure_credit_purchases(existing_tables)
    _ensure_leads(existing_tables)
    _ensure_click_stats(existing_tables)


def downgrade() -> None:
    """Keep the initial baseline downgrade non-destructive for existing SaaS data."""
    pass
