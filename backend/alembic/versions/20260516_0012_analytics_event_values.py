"""Add aggregate values to click stats.

Revision ID: 20260516_0012
Revises: 20260511_0011
Create Date: 2026-05-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260516_0012"
down_revision = "20260511_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("click_stats", sa.Column("value_sum", sa.Integer(), server_default="0", nullable=False))
    op.add_column("click_stats", sa.Column("value_count", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("click_stats", "value_count")
    op.drop_column("click_stats", "value_sum")
