"""Add username/password account fields.

Revision ID: 20260507_0007
Revises: 20260427_0006
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260507_0007"
down_revision = "20260427_0006"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    cols = _columns("users")
    if "username" not in cols:
        op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    if "password" not in cols:
        op.add_column("users", sa.Column("password", sa.String(length=255), nullable=True))
    if "created_at" not in cols:
        op.add_column(
            "users",
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    op.execute(sa.text('CREATE UNIQUE INDEX IF NOT EXISTS "ix_users_username" ON "users" ("username")'))


def downgrade() -> None:
    op.execute(sa.text('DROP INDEX IF EXISTS "ix_users_username"'))
    cols = _columns("users")
    if "password" in cols:
        op.drop_column("users", "password")
    if "username" in cols:
        op.drop_column("users", "username")
