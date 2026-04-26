"""Normalize payment statuses and enforce webhook event idempotency.

Revision ID: 20260426_0002
Revises: 20260425_0001
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260426_0002"
down_revision = "20260425_0001"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


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


def upgrade() -> None:
    if not _table_exists("credit_purchases"):
        return

    cols = _columns("credit_purchases")
    if "webhook_event_id" not in cols:
        op.add_column("credit_purchases", sa.Column("webhook_event_id", sa.String(length=128), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE credit_purchases
            SET status = CASE
                WHEN lower(status) IN ('created', 'pending') THEN 'pending'
                WHEN lower(status) IN ('completed', 'paid', 'succeeded', 'success') THEN 'paid'
                WHEN lower(status) IN ('failed', 'canceled', 'cancelled') THEN 'failed'
                WHEN lower(status) = 'expired' THEN 'expired'
                WHEN lower(status) = 'refunded' THEN 'refunded'
                ELSE lower(status)
            END
            WHERE status IS NOT NULL
            """
        )
    )

    if not _constraint_exists("uq_credit_purchase_webhook_event_id"):
        op.create_unique_constraint(
            "uq_credit_purchase_webhook_event_id",
            "credit_purchases",
            ["webhook_event_id"],
        )
    op.execute(
        sa.text(
            'CREATE INDEX IF NOT EXISTS "ix_credit_purchases_webhook_event_id" '
            'ON "credit_purchases" (webhook_event_id)'
        )
    )


def downgrade() -> None:
    if not _table_exists("credit_purchases"):
        return

    op.execute(sa.text('DROP INDEX IF EXISTS "ix_credit_purchases_webhook_event_id"'))
    if _constraint_exists("uq_credit_purchase_webhook_event_id"):
        op.drop_constraint("uq_credit_purchase_webhook_event_id", "credit_purchases", type_="unique")
