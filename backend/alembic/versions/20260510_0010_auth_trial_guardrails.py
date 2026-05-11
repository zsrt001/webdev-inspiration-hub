"""Add auth and starter credit guardrails.

Revision ID: 20260510_0010
Revises: 20260510_0009
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0010"
down_revision = "20260510_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_transactions_welcome_once
            ON credit_transactions (user_id)
            WHERE transaction_type = 'WELCOME_BONUS'
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_credit_transactions_welcome_once"))
