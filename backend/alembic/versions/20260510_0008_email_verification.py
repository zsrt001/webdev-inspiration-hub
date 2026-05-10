"""Add email_verified_at and unique constraint on email.

Revision ID: 20260510_0008
Revises: 20260507_0007
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0008"
down_revision = "20260507_0007"
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
    if "email_verified_at" not in cols:
        op.add_column(
            "users",
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute(sa.text('CREATE UNIQUE INDEX IF NOT EXISTS "ix_users_email_unique" ON "users" ("email")'))


def downgrade() -> None:
    op.execute(sa.text('DROP INDEX IF EXISTS "ix_users_email_unique"'))
    cols = _columns("users")
    if "email_verified_at" in cols:
        op.drop_column("users", "email_verified_at")
