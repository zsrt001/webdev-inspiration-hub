"""Add durable remote join sessions.

Revision ID: 20260510_0009
Revises: 20260510_0008
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0009"
down_revision = "20260510_0008"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if not _table_exists("remote_join_sessions"):
        op.create_table(
            "remote_join_sessions",
            sa.Column("session_id", sa.String(length=16), primary_key=True, nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("order_id", sa.String(length=64), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    op.execute(
        sa.text(
            'CREATE INDEX IF NOT EXISTS "ix_remote_join_sessions_order_id" '
            'ON "remote_join_sessions" ("order_id")'
        )
    )
    op.execute(
        sa.text(
            'CREATE INDEX IF NOT EXISTS "ix_remote_join_sessions_expires_at" '
            'ON "remote_join_sessions" ("expires_at")'
        )
    )


def downgrade() -> None:
    op.drop_index("ix_remote_join_sessions_expires_at", table_name="remote_join_sessions")
    op.drop_index("ix_remote_join_sessions_order_id", table_name="remote_join_sessions")
    op.drop_table("remote_join_sessions")
