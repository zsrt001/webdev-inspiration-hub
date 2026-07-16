"""Repair the aggregate analytics columns for databases stamped at revision 0012.

Revision ID: 20260712_0014
Revises: 20260710_0013
Create Date: 2026-07-12 00:00:00.000000
"""

from alembic import op


revision = "20260712_0014"
down_revision = "20260710_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $repair_click_stats_values$
        DECLARE
            incompatible_columns text;
        BEGIN
            SELECT string_agg(column_name, ', ' ORDER BY column_name)
            INTO incompatible_columns
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'click_stats'
              AND column_name IN ('value_sum', 'value_count')
              AND data_type <> 'integer';

            IF incompatible_columns IS NOT NULL THEN
                RAISE EXCEPTION
                    'click_stats aggregate columns have incompatible types: %',
                    incompatible_columns
                    USING ERRCODE = '42804';
            END IF;
        END
        $repair_click_stats_values$
        """
    )
    op.execute(
        "ALTER TABLE click_stats "
        "ADD COLUMN IF NOT EXISTS value_sum INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE click_stats "
        "ADD COLUMN IF NOT EXISTS value_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        """
        UPDATE click_stats
        SET value_sum = COALESCE(value_sum, 0),
            value_count = COALESCE(value_count, 0)
        WHERE value_sum IS NULL OR value_count IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE click_stats
          ALTER COLUMN value_sum SET DEFAULT 0,
          ALTER COLUMN value_sum SET NOT NULL,
          ALTER COLUMN value_count SET DEFAULT 0,
          ALTER COLUMN value_count SET NOT NULL
        """
    )


def downgrade() -> None:
    # Forward-only repair: these columns belong to revision 0012 and may have
    # existed before this migration, so removing them would be destructive.
    pass
