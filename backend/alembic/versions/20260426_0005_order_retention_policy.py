"""Add order retention and cleanup fields.

Revision ID: 20260426_0005
Revises: 20260426_0004
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260426_0005"
down_revision = "20260426_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("source_images_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "orders",
        sa.Column("storage_cleanup_status", sa.String(length=32), server_default="active", nullable=False),
    )
    op.create_index("ix_orders_source_images_expires_at", "orders", ["source_images_expires_at"])
    op.create_index("ix_orders_expires_at", "orders", ["expires_at"])
    op.create_index("ix_orders_deleted_at", "orders", ["deleted_at"])
    op.create_index("ix_orders_storage_cleanup_status", "orders", ["storage_cleanup_status"])

    op.execute(
        """
        UPDATE orders
        SET
          source_images_expires_at = COALESCE(source_images_expires_at, created_at + interval '7 days'),
          expires_at = COALESCE(expires_at, created_at + interval '30 days'),
          storage_cleanup_status = COALESCE(storage_cleanup_status, 'active')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_orders_storage_cleanup_status", table_name="orders")
    op.drop_index("ix_orders_deleted_at", table_name="orders")
    op.drop_index("ix_orders_expires_at", table_name="orders")
    op.drop_index("ix_orders_source_images_expires_at", table_name="orders")
    op.drop_column("orders", "storage_cleanup_status")
    op.drop_column("orders", "deleted_at")
    op.drop_column("orders", "expires_at")
    op.drop_column("orders", "source_images_expires_at")
