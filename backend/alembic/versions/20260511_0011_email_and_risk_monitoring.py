"""Add email delivery and account risk monitoring.

Revision ID: 20260511_0011
Revises: 20260510_0010
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260511_0011"
down_revision = "20260510_0010"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if not _table_exists("email_delivery_logs"):
        op.create_table(
            "email_delivery_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("purpose", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("to_email", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if not _table_exists("account_risk_events"):
        op.create_table(
            "account_risk_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=True),
            sa.Column("ip_hash", sa.String(length=64), nullable=True),
            sa.Column("device_hash", sa.String(length=64), nullable=True),
            sa.Column("email_hash", sa.String(length=64), nullable=True),
            sa.Column("email_domain", sa.String(length=255), nullable=True),
            sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    for table, columns in {
        "email_delivery_logs": ("purpose", "to_email", "status", "provider_message_id", "error_code", "created_at"),
        "account_risk_events": (
            "user_id",
            "event_type",
            "provider",
            "ip_hash",
            "device_hash",
            "email_hash",
            "email_domain",
            "created_at",
        ),
    }.items():
        for column in columns:
            op.execute(sa.text(f'CREATE INDEX IF NOT EXISTS "ix_{table}_{column}" ON "{table}" ("{column}")'))


def downgrade() -> None:
    op.drop_table("account_risk_events")
    op.drop_table("email_delivery_logs")
