"""Add the production-only GOOGLE_AUTH_ONLY release activation kind.

Revision ID: 20260710_0021
Revises: 20260710_0020
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260710_0021"
down_revision = "20260710_0020"
branch_labels = None
depends_on = None


_KINDS_WITH_GOOGLE_AUTH_ONLY = (
    "kind IN ('SAFE_BASELINE_INSTALL', 'PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL', "
    "'COMMERCIAL_7A', 'GOOGLE_AUTH_ONLY', 'CONTRACT_7B')"
)
_ENVIRONMENT_KINDS_WITH_GOOGLE_AUTH_ONLY = (
    "((environment = 'preview' AND kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')) OR "
    "(environment = 'production' AND kind IN "
    "('SAFE_BASELINE_INSTALL', 'COMMERCIAL_7A', 'GOOGLE_AUTH_ONLY', 'CONTRACT_7B')))"
)
_ORIGINAL_KINDS = (
    "kind IN ('SAFE_BASELINE_INSTALL', 'PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL', "
    "'COMMERCIAL_7A', 'CONTRACT_7B')"
)
_ORIGINAL_ENVIRONMENT_KINDS = (
    "((environment = 'preview' AND kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')) OR "
    "(environment = 'production' AND kind IN "
    "('SAFE_BASELINE_INSTALL', 'COMMERCIAL_7A', 'CONTRACT_7B')))"
)


def _replace_constraints(kind_expression: str, environment_expression: str) -> None:
    op.drop_constraint(
        "ck_release_activation_environment_kind",
        "release_activations",
        type_="check",
    )
    op.drop_constraint(
        "ck_release_activation_kind",
        "release_activations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_release_activation_kind",
        "release_activations",
        sa.text(kind_expression),
    )
    op.create_check_constraint(
        "ck_release_activation_environment_kind",
        "release_activations",
        sa.text(environment_expression),
    )


def upgrade() -> None:
    _replace_constraints(
        _KINDS_WITH_GOOGLE_AUTH_ONLY,
        _ENVIRONMENT_KINDS_WITH_GOOGLE_AUTH_ONLY,
    )


def downgrade() -> None:
    connection = op.get_bind()
    count = int(
        connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM release_activations "
                "WHERE kind = 'GOOGLE_AUTH_ONLY'"
            )
        ).scalar_one()
    )
    if count:
        raise RuntimeError(
            "cannot downgrade while GOOGLE_AUTH_ONLY activation audit rows exist"
        )
    _replace_constraints(_ORIGINAL_KINDS, _ORIGINAL_ENVIRONMENT_KINDS)
