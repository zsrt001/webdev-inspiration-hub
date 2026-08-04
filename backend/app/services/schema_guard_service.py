"""Read-only migration and schema validation for runtime readiness."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_MINIMUM_SCHEMA_REVISION = "20260712_0014"
# Only schema heads tied to an approved runtime bundle are accepted. Intermediate
# Alembic revisions are migration states, not deployable runtime contracts.
_RUNTIME_COMPATIBLE_SCHEMA_REVISIONS = frozenset(
    {
        _MINIMUM_SCHEMA_REVISION,
        "20260710_0020",
    }
)
_REQUIRED_TABLES = frozenset(
    {
        "users",
        "credit_transactions",
        "admin_audit_logs",
        "remote_join_sessions",
        "email_delivery_logs",
        "account_risk_events",
        "release_activations",
        "acceptance_identity_bindings",
        "ops_feature_flags",
        "ops_feature_flag_audits",
        "data_migration_runs",
        "data_migration_checkpoints",
        "release_observation_runs",
        "release_observation_samples",
    }
)
_REQUIRED_0020_TABLES = frozenset(
    {
        "release_observation_recoveries",
        "release_auth_origin_leases",
    }
)
_REQUIRED_USER_COLUMNS = frozenset(
    {
        "username",
        "password",
        "auth_provider",
        "auth_subject",
        "email",
        "email_verified_at",
        "role",
        "status",
        "last_login_at",
        "nickname",
        "avatar_url",
        "updated_at",
    }
)
_REQUIRED_CLICK_STATS_COLUMNS = frozenset({"value_sum", "value_count"})
_REQUIRED_INDEXES = frozenset(
    {
        "ix_users_username",
        "ix_users_auth_provider",
        "ix_users_auth_subject",
        "ix_users_email",
        "ix_users_role",
        "ix_users_status",
        "ix_admin_audit_logs_actor",
        "ix_admin_audit_logs_action",
        "ix_admin_audit_logs_request_path",
        "ix_admin_audit_logs_created_at",
        "ix_remote_join_sessions_order_id",
        "ix_remote_join_sessions_expires_at",
        "ix_email_delivery_logs_purpose",
        "ix_email_delivery_logs_to_email",
        "ix_email_delivery_logs_status",
        "ix_email_delivery_logs_provider_message_id",
        "ix_email_delivery_logs_error_code",
        "ix_email_delivery_logs_created_at",
        "ix_account_risk_events_user_id",
        "ix_account_risk_events_event_type",
        "ix_account_risk_events_provider",
        "ix_account_risk_events_ip_hash",
        "ix_account_risk_events_device_hash",
        "ix_account_risk_events_email_hash",
        "ix_account_risk_events_email_domain",
        "ix_account_risk_events_created_at",
        "ux_credit_transactions_welcome_once",
        "ux_credit_transactions_order_refund_once",
        "uq_release_activation_runtime_bundle",
        "uq_release_activation_active_source",
        "uq_release_activation_production_safe_baseline",
        "uq_release_activation_fault_intent_id",
        "uq_release_activation_fault_intent_sha256",
        "ix_ops_feature_flag_audits_created_at",
        "uq_data_migration_parent_release",
        "uq_data_migration_child_contract",
    }
)

_runtime_schema_validated = False
_user_account_schema_validated = False


async def validate_runtime_schema(db: AsyncSession) -> None:
    """Fail if the database has not been migrated to the runtime contract."""
    global _runtime_schema_validated, _user_account_schema_validated
    if _runtime_schema_validated:
        return

    revision_result = await db.execute(text("SELECT version_num FROM alembic_version"))
    revisions = sorted(str(value) for value in revision_result.scalars().all() if value)

    table_result = await db.execute(
        text(
            """
            SELECT relation.relname AS table_name
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relkind IN ('r', 'p')
            """
        )
    )
    tables = {str(value) for value in table_result.scalars().all()}

    index_result = await db.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
            """
        )
    )
    indexes = {str(value) for value in index_result.scalars().all()}

    column_result = await db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'users'
            """
        )
    )
    user_columns = {str(value) for value in column_result.scalars().all()}

    click_stats_column_result = await db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'click_stats'
            """
        )
    )
    click_stats_columns = {str(value) for value in click_stats_column_result.scalars().all()}

    problems: list[str] = []
    if (
        len(revisions) != 1
        or revisions[0] not in _RUNTIME_COMPATIBLE_SCHEMA_REVISIONS
    ):
        problems.append(
            "revision must be on the runtime-compatible Alembic chain from "
            f"{_MINIMUM_SCHEMA_REVISION}; found "
            f"{', '.join(revisions) or 'none'}"
        )
    required_tables = _REQUIRED_TABLES
    if revisions == ["20260710_0020"]:
        required_tables = required_tables | _REQUIRED_0020_TABLES
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        problems.append(f"missing tables: {', '.join(missing_tables)}")
    missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        problems.append(f"missing indexes: {', '.join(missing_indexes)}")
    missing_columns = sorted(_REQUIRED_USER_COLUMNS - user_columns)
    if missing_columns:
        problems.append(f"users missing columns: {', '.join(missing_columns)}")
    missing_click_stats_columns = sorted(_REQUIRED_CLICK_STATS_COLUMNS - click_stats_columns)
    if missing_click_stats_columns:
        problems.append(f"click_stats missing columns: {', '.join(missing_click_stats_columns)}")

    if problems:
        raise RuntimeError(f"database_schema_incomplete: {'; '.join(problems)}")

    _runtime_schema_validated = True
    _user_account_schema_validated = True


async def ensure_user_account_columns(db: AsyncSession) -> None:
    """Compatibility entry point backed by the same read-only readiness check."""
    if _user_account_schema_validated:
        return
    await validate_runtime_schema(db)
