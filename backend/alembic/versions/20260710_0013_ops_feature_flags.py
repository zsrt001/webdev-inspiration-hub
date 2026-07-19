"""Add audited capability flags and durable release control-plane state.

Revision ID: 20260710_0013
Revises: 20260516_0012
Create Date: 2026-07-10
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0013"
down_revision = "20260516_0012"
branch_labels = None
depends_on = None


CAPABILITIES = (
    "google_auth",
    "authenticated_upload",
    "generation",
    "credit_pack_checkout",
    "subscription_billing",
    "private_download",
    "partner_invite",
)

CONTROL_PLANE_TABLES = (
    "release_observation_samples",
    "release_observation_runs",
    "data_migration_checkpoints",
    "data_migration_runs",
    "ops_feature_flag_audits",
    "ops_feature_flags",
    "acceptance_identity_bindings",
    "release_activations",
)


def _sha_check(column: str) -> str:
    return f"{column} IS NULL OR {column} ~ '^[0-9a-f]{{64}}$'"


def _reconcile_legacy_baseline() -> None:
    """Move former runtime-DDL compatibility into one reviewed migration."""
    for statement in (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(32)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_subject VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        'CREATE INDEX IF NOT EXISTS "ix_users_username" ON users (username)',
        'CREATE INDEX IF NOT EXISTS "ix_users_auth_provider" ON users (auth_provider)',
        'CREATE INDEX IF NOT EXISTS "ix_users_auth_subject" ON users (auth_subject)',
        'CREATE INDEX IF NOT EXISTS "ix_users_email" ON users (email)',
        'CREATE INDEX IF NOT EXISTS "ix_users_role" ON users (role)',
        'CREATE INDEX IF NOT EXISTS "ix_users_status" ON users (status)',
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_transactions_order_refund_once
        ON credit_transactions (user_id, source_id)
        WHERE transaction_type = 'GENERATION_REFUND'
          AND source = 'order'
          AND source_id IS NOT NULL
        """,
    ):
        op.execute(sa.text(statement))


def _create_release_activations() -> None:
    op.create_table(
        "release_activations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_sha", sa.String(64), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(80), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("build_artifact_id", sa.String(32), nullable=True),
        sa.Column("build_artifact_digest", sa.String(71), nullable=True),
        sa.Column("report_sha256", sa.String(64), nullable=True),
        sa.Column("api_deployment_id", sa.String(160), nullable=True),
        sa.Column("api_deployment_url", sa.String(512), nullable=True),
        sa.Column("api_role", sa.String(64), nullable=True),
        sa.Column("worker_deployment_id", sa.String(160), nullable=True),
        sa.Column("worker_role", sa.String(64), nullable=True),
        sa.Column("worker_image_digest", sa.String(80), nullable=True),
        sa.Column("private_evidence_prefix", sa.String(512), nullable=True),
        sa.Column("workflow_run_id", sa.String(128), nullable=False),
        sa.Column("workflow_attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="RESERVED"),
        sa.Column("phase_rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approval", sa.String(160), nullable=False),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("target_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("acceptance_fault_intent_id", sa.String(128), nullable=True),
        sa.Column("acceptance_fault_intent_sha256", sa.String(64), nullable=True),
        sa.Column("acceptance_fault_state", sa.String(32), nullable=True),
        sa.Column("acceptance_fault_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acceptance_fault_cleanup_claim_id", sa.String(128), nullable=True),
        sa.Column("acceptance_fault_cleanup_fencing_token", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("environment IN ('preview', 'production')", name="ck_release_activation_environment"),
        sa.CheckConstraint(
            "kind IN ('SAFE_BASELINE_INSTALL', 'PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL', "
            "'COMMERCIAL_7A', 'CONTRACT_7B')",
            name="ck_release_activation_kind",
        ),
        sa.CheckConstraint(
            "((environment = 'preview' AND kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')) OR "
            "(environment = 'production' AND kind IN ('SAFE_BASELINE_INSTALL', 'COMMERCIAL_7A', 'CONTRACT_7B')))",
            name="ck_release_activation_environment_kind",
        ),
        sa.CheckConstraint("source_sha ~ '^[0-9a-f]{40,64}$'", name="ck_release_activation_source_sha"),
        sa.CheckConstraint(_sha_check("manifest_sha256"), name="ck_release_activation_manifest_sha256"),
        sa.CheckConstraint(
            "(build_artifact_id IS NULL AND build_artifact_digest IS NULL) OR "
            "(build_artifact_id ~ '^[1-9][0-9]{0,19}$' AND "
            "build_artifact_digest ~ '^sha256:[0-9a-f]{64}$')",
            name="ck_release_activation_build_artifact",
        ),
        sa.CheckConstraint(_sha_check("report_sha256"), name="ck_release_activation_report_sha256"),
        sa.CheckConstraint(_sha_check("current_snapshot_hash"), name="ck_release_activation_current_snapshot"),
        sa.CheckConstraint(_sha_check("target_snapshot_hash"), name="ck_release_activation_target_snapshot"),
        sa.CheckConstraint(
            "(api_deployment_id IS NULL AND api_deployment_url IS NULL) OR "
            "(api_deployment_id IS NOT NULL AND api_deployment_url IS NOT NULL)",
            name="ck_release_activation_api_deployment_pair",
        ),
        sa.CheckConstraint(
            "api_deployment_url IS NULL OR api_deployment_url ~ '^https://[^[:space:]]+$'",
            name="ck_release_activation_api_deployment_url",
        ),
        sa.CheckConstraint(
            "reservation_expires_at IS NULL OR "
            "(reservation_expires_at > created_at AND "
            "reservation_expires_at <= created_at + INTERVAL '2 hours')",
            name="ck_release_activation_reservation_ttl",
        ),
        sa.CheckConstraint(
            "version > 0 AND workflow_attempt > 0 AND phase_rank >= 0",
            name="ck_release_activation_versions",
        ),
        sa.CheckConstraint(
            "((acceptance_fault_intent_id IS NULL AND acceptance_fault_intent_sha256 IS NULL "
            "AND acceptance_fault_state IS NULL AND acceptance_fault_expires_at IS NULL "
            "AND acceptance_fault_cleanup_claim_id IS NULL AND acceptance_fault_cleanup_fencing_token IS NULL) OR "
            "(acceptance_fault_intent_id IS NOT NULL AND acceptance_fault_intent_sha256 IS NOT NULL "
            "AND acceptance_fault_state IN ('PREPARED', 'ARMED', 'CLEANUP_CLAIMED', 'DISARMED') "
            "AND acceptance_fault_expires_at IS NOT NULL AND "
            "((acceptance_fault_cleanup_claim_id IS NULL AND acceptance_fault_cleanup_fencing_token IS NULL "
            "AND acceptance_fault_state IN ('PREPARED', 'ARMED', 'DISARMED')) OR "
            "(acceptance_fault_cleanup_claim_id IS NOT NULL AND acceptance_fault_cleanup_fencing_token IS NOT NULL "
            "AND acceptance_fault_cleanup_fencing_token > 0 "
            "AND acceptance_fault_state IN ('CLEANUP_CLAIMED', 'DISARMED')))))",
            name="ck_release_activation_fault_complete",
        ),
        sa.CheckConstraint(
            "acceptance_fault_state IS NULL OR (environment = 'production' AND kind = 'COMMERCIAL_7A')",
            name="ck_release_activation_fault_role",
        ),
        sa.CheckConstraint(
            "acceptance_fault_expires_at IS NULL OR "
            "(acceptance_fault_expires_at > created_at AND acceptance_fault_expires_at <= created_at + INTERVAL '300 seconds')",
            name="ck_release_activation_fault_ttl",
        ),
        sa.CheckConstraint(_sha_check("acceptance_fault_intent_sha256"), name="ck_release_activation_fault_sha256"),
    )
    op.create_index(
        "uq_release_activation_runtime_bundle",
        "release_activations",
        ["environment", "kind", "runtime_bundle_id"],
        unique=True,
        postgresql_where=sa.text("runtime_bundle_id IS NOT NULL"),
    )
    op.create_index(
        "uq_release_activation_active_source",
        "release_activations",
        ["environment", "kind", "source_sha"],
        unique=True,
        postgresql_where=sa.text(
            "phase NOT IN ('COMPLETED', 'CLEANED', 'PASSED', 'FAILED', 'DISARMED', 'PRODUCTION_ACCEPTED')"
        ),
    )
    op.create_index(
        "uq_release_activation_production_safe_baseline",
        "release_activations",
        ["environment", "kind"],
        unique=True,
        postgresql_where=sa.text("environment = 'production' AND kind = 'SAFE_BASELINE_INSTALL'"),
    )
    op.create_index(
        "uq_release_activation_fault_intent_id",
        "release_activations",
        ["acceptance_fault_intent_id"],
        unique=True,
        postgresql_where=sa.text("acceptance_fault_intent_id IS NOT NULL"),
    )
    op.create_index(
        "uq_release_activation_fault_intent_sha256",
        "release_activations",
        ["acceptance_fault_intent_sha256"],
        unique=True,
        postgresql_where=sa.text("acceptance_fault_intent_sha256 IS NOT NULL"),
    )


def _create_identity_and_flags() -> None:
    op.create_table(
        "acceptance_identity_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("subject_hmac", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("deployment_id", sa.String(160), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("consumed_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "environment", "deployment_id", "provider", "subject_hmac",
            name="uq_acceptance_identity_binding_coordinate",
        ),
        sa.CheckConstraint("environment IN ('preview', 'production')", name="ck_acceptance_binding_environment"),
        sa.CheckConstraint("subject_hmac ~ '^[0-9a-f]{64}$'", name="ck_acceptance_binding_subject_hmac"),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + INTERVAL '86400 seconds'",
            name="ck_acceptance_binding_ttl",
        ),
        sa.CheckConstraint(
            "(consumed_user_id IS NULL AND consumed_at IS NULL) OR "
            "(consumed_user_id IS NOT NULL AND consumed_at IS NOT NULL)",
            name="ck_acceptance_binding_consumed_pair",
        ),
    )

    op.create_table(
        "ops_feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="OFF"),
        sa.Column("deployment_id", sa.String(160), nullable=True),
        sa.Column("runtime_bundle_id", sa.String(80), nullable=True),
        sa.Column("worker_image_digest", sa.String(80), nullable=True),
        sa.Column(
            "release_activation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("cohort_user_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "verified_identity_hashes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("environment", "capability", name="uq_ops_feature_flag_environment_capability"),
        sa.CheckConstraint("environment IN ('preview', 'production')", name="ck_ops_feature_flag_environment"),
        sa.CheckConstraint(
            "capability IN ('google_auth', 'authenticated_upload', 'generation', 'credit_pack_checkout', "
            "'subscription_billing', 'private_download', 'partner_invite')",
            name="ck_ops_feature_flag_capability",
        ),
        sa.CheckConstraint("state IN ('OFF', 'ACCEPTANCE_COHORT', 'ON')", name="ck_ops_feature_flag_state"),
        sa.CheckConstraint(
            "state = 'OFF' OR (deployment_id IS NOT NULL AND runtime_bundle_id IS NOT NULL "
            "AND release_activation_id IS NOT NULL)",
            name="ck_ops_feature_flag_state_coordinates",
        ),
        sa.CheckConstraint(
            "((state = 'ACCEPTANCE_COHORT' AND expires_at IS NOT NULL "
            "AND expires_at > updated_at AND expires_at <= updated_at + INTERVAL '86400 seconds') OR "
            "(state <> 'ACCEPTANCE_COHORT' AND expires_at IS NULL))",
            name="ck_ops_feature_flag_cohort_ttl",
        ),
        sa.CheckConstraint("jsonb_typeof(cohort_user_ids) = 'array'", name="ck_ops_feature_flag_cohort_array"),
        sa.CheckConstraint(
            "jsonb_typeof(verified_identity_hashes) = 'array'", name="ck_ops_feature_flag_identity_array"
        ),
        sa.CheckConstraint(_sha_check("target_manifest_sha256"), name="ck_ops_feature_flag_manifest_sha256"),
        sa.CheckConstraint("version > 0", name="ck_ops_feature_flag_version"),
    )

    op.create_table(
        "ops_feature_flag_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "feature_flag_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ops_feature_flags.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("old_state", sa.String(32), nullable=False),
        sa.Column("new_state", sa.String(32), nullable=False),
        sa.Column("old_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("new_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("deployment_id", sa.String(160), nullable=True),
        sa.Column("runtime_bundle_id", sa.String(80), nullable=True),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("details_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_feature_flag_audits_created_at", "ops_feature_flag_audits", ["created_at"])

    seed_values = ",\n".join(
        "(" + ", ".join(
            (
                f"'{uuid.uuid4()}'::uuid",
                f"'{environment}'",
                f"'{capability}'",
                "'OFF'",
                "'[]'::jsonb",
                "'[]'::jsonb",
                "1",
            )
        ) + ")"
        for environment in ("preview", "production")
        for capability in CAPABILITIES
    )
    op.execute(
        sa.text(
            "INSERT INTO ops_feature_flags "
            "(id, environment, capability, state, cohort_user_ids, verified_identity_hashes, version) VALUES\n"
            + seed_values
        )
    )


def _create_migration_control_plane() -> None:
    op.create_table(
        "data_migration_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "parent_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_migration_runs.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column(
            "release_activation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(80), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("inventory_sha256", sa.String(64), nullable=False),
        sa.Column("script_sha256", sa.String(64), nullable=True),
        sa.Column("source_revision", sa.String(64), nullable=True),
        sa.Column("target_revision", sa.String(64), nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("approval", sa.String(160), nullable=False),
        sa.Column("lease_owner", sa.String(160), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("counts_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("environment IN ('preview', 'production')", name="ck_data_migration_run_environment"),
        sa.CheckConstraint("fencing_token > 0", name="ck_data_migration_run_fencing_token"),
        sa.CheckConstraint("lease_expires_at > heartbeat_at", name="ck_data_migration_run_lease"),
        sa.CheckConstraint(
            "(parent_run_id IS NULL AND script_sha256 IS NULL) OR "
            "(parent_run_id IS NOT NULL AND script_sha256 IS NOT NULL)",
            name="ck_data_migration_run_parent_child",
        ),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_data_migration_run_manifest_sha"),
        sa.CheckConstraint("inventory_sha256 ~ '^[0-9a-f]{64}$'", name="ck_data_migration_run_inventory_sha"),
        sa.CheckConstraint(_sha_check("script_sha256"), name="ck_data_migration_run_script_sha"),
        sa.UniqueConstraint(
            "release_activation_id", "parent_run_id", "script_sha256", "mode", "approval",
            name="uq_data_migration_run_contract",
        ),
    )
    op.create_index(
        "uq_data_migration_parent_release",
        "data_migration_runs",
        ["release_activation_id", "mode"],
        unique=True,
        postgresql_where=sa.text("parent_run_id IS NULL"),
    )
    op.create_index(
        "uq_data_migration_child_contract",
        "data_migration_runs",
        ["parent_run_id", "script_sha256", "mode", "approval"],
        unique=True,
        postgresql_where=sa.text("parent_run_id IS NOT NULL"),
    )
    op.create_table(
        "data_migration_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("data_migration_runs.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("script_sha256", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("batch_boundary", sa.String(256), nullable=False),
        sa.Column("inventory_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("approval", sa.String(160), nullable=False),
        sa.Column("counts_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "run_id", "script_sha256", "mode", "batch_boundary",
            name="uq_data_migration_checkpoint_boundary",
        ),
        sa.CheckConstraint("script_sha256 ~ '^[0-9a-f]{64}$'", name="ck_data_migration_checkpoint_script_sha"),
        sa.CheckConstraint("inventory_sha256 ~ '^[0-9a-f]{64}$'", name="ck_data_migration_checkpoint_inventory_sha"),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_data_migration_checkpoint_manifest_sha"),
    )


def _create_observation_control_plane() -> None:
    op.create_table(
        "release_observation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "release_activation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_activations.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(80), nullable=False),
        sa.Column("api_deployment_id", sa.String(160), nullable=False),
        sa.Column("worker_deployment_id", sa.String(160), nullable=True),
        sa.Column("worker_image_digest", sa.String(80), nullable=True),
        sa.Column("current_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("target_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="OBSERVING"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_cycle_sha256", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("finalizer", sa.String(160), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('OBSERVING', 'FINALIZING', 'PASSED', 'FAILED')",
            name="ck_release_observation_state",
        ),
        sa.CheckConstraint("deadline_at > started_at", name="ck_release_observation_deadline"),
        sa.CheckConstraint("version > 0", name="ck_release_observation_version"),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_release_observation_manifest_sha"),
        sa.CheckConstraint("current_snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_release_observation_current_snapshot"),
        sa.CheckConstraint("target_snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_release_observation_target_snapshot"),
        sa.CheckConstraint(_sha_check("cleanup_cycle_sha256"), name="ck_release_observation_cleanup_sha"),
        sa.UniqueConstraint("release_activation_id", name="uq_release_observation_activation"),
    )
    op.create_table(
        "release_observation_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "observation_run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_observation_runs.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("bucket_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_sha256", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(512), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "observation_run_id", "bucket_started_at", name="uq_release_observation_sample_bucket"
        ),
        sa.CheckConstraint("sample_sha256 ~ '^[0-9a-f]{64}$'", name="ck_release_observation_sample_sha"),
        sa.CheckConstraint("length(signature) >= 32", name="ck_release_observation_sample_signature"),
    )


def _install_database_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_control_plane_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$
            """
        )
    )
    for table in ("ops_feature_flag_audits", "data_migration_checkpoints", "release_observation_samples"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION prevent_control_plane_mutation()"
            )
        )

    for table in (
        "ops_feature_flags",
        "release_activations",
        "acceptance_identity_bindings",
        "data_migration_runs",
        "release_observation_runs",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION prevent_control_plane_mutation()"
            )
        )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_feature_flag_cas()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'ops_feature_flags requires version CAS';
              END IF;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_ops_feature_flags_cas BEFORE UPDATE ON ops_feature_flags "
            "FOR EACH ROW EXECUTE FUNCTION enforce_feature_flag_cas()"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_release_activation_regression()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.phase IN (
                'COMPLETED', 'CLEANED', 'PASSED', '7A_ACCEPTED', 'PRODUCTION_ACCEPTED'
              ) THEN
                RAISE EXCEPTION 'terminal release activation is immutable';
              END IF;
              IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'release activation requires version CAS';
              END IF;
              IF NEW.phase IS DISTINCT FROM OLD.phase AND NEW.phase_rank <= OLD.phase_rank THEN
                RAISE EXCEPTION 'release activation phase rank must advance';
              END IF;
              IF NEW.phase IS NOT DISTINCT FROM OLD.phase AND NEW.phase_rank <> OLD.phase_rank THEN
                RAISE EXCEPTION 'release activation phase rank cannot drift without a phase change';
              END IF;
              IF OLD.runtime_bundle_id IS NOT NULL
                 AND NEW.runtime_bundle_id IS DISTINCT FROM OLD.runtime_bundle_id THEN
                RAISE EXCEPTION 'runtime bundle is immutable once assigned';
              END IF;
              IF OLD.manifest_sha256 IS NOT NULL
                 AND NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256 THEN
                RAISE EXCEPTION 'manifest is immutable once assigned';
              END IF;
              IF OLD.build_artifact_id IS NOT NULL
                 AND (NEW.build_artifact_id IS DISTINCT FROM OLD.build_artifact_id
                      OR NEW.build_artifact_digest IS DISTINCT FROM OLD.build_artifact_digest) THEN
                RAISE EXCEPTION 'build artifact coordinates are immutable once assigned';
              END IF;
              IF OLD.api_deployment_id IS NOT NULL
                 AND NEW.api_deployment_id IS DISTINCT FROM OLD.api_deployment_id THEN
                RAISE EXCEPTION 'API deployment ID is immutable once assigned';
              END IF;
              IF OLD.api_deployment_url IS NOT NULL
                 AND NEW.api_deployment_url IS DISTINCT FROM OLD.api_deployment_url THEN
                RAISE EXCEPTION 'API deployment URL is immutable once assigned';
              END IF;
              IF OLD.reservation_expires_at IS NOT NULL
                 AND NEW.reservation_expires_at IS DISTINCT FROM OLD.reservation_expires_at THEN
                RAISE EXCEPTION 'reservation expiry is immutable once assigned';
              END IF;
              IF OLD.acceptance_fault_state IS NULL AND NEW.acceptance_fault_state IS NOT NULL
                 AND NEW.acceptance_fault_state <> 'PREPARED' THEN
                RAISE EXCEPTION 'fault intent must start PREPARED';
              END IF;
              IF OLD.acceptance_fault_state IS NOT NULL THEN
                IF NEW.acceptance_fault_intent_id IS DISTINCT FROM OLD.acceptance_fault_intent_id
                   OR NEW.acceptance_fault_intent_sha256 IS DISTINCT FROM OLD.acceptance_fault_intent_sha256
                   OR NEW.acceptance_fault_expires_at IS DISTINCT FROM OLD.acceptance_fault_expires_at THEN
                  RAISE EXCEPTION 'prepared fault intent coordinates are immutable';
                END IF;
                IF OLD.acceptance_fault_cleanup_claim_id IS NOT NULL AND
                   (NEW.acceptance_fault_cleanup_claim_id IS DISTINCT FROM OLD.acceptance_fault_cleanup_claim_id OR
                    NEW.acceptance_fault_cleanup_fencing_token IS DISTINCT FROM OLD.acceptance_fault_cleanup_fencing_token) THEN
                  RAISE EXCEPTION 'cleanup claim fence is immutable';
                END IF;
                IF NOT (
                  (OLD.acceptance_fault_state = 'PREPARED' AND NEW.acceptance_fault_state IN ('PREPARED', 'ARMED', 'CLEANUP_CLAIMED')) OR
                  (OLD.acceptance_fault_state = 'ARMED' AND NEW.acceptance_fault_state IN ('ARMED', 'DISARMED', 'CLEANUP_CLAIMED')) OR
                  (OLD.acceptance_fault_state = 'CLEANUP_CLAIMED' AND NEW.acceptance_fault_state IN ('CLEANUP_CLAIMED', 'DISARMED')) OR
                  (OLD.acceptance_fault_state = 'DISARMED' AND NEW.acceptance_fault_state = 'DISARMED')
                ) THEN
                  RAISE EXCEPTION 'invalid fault intent transition';
                END IF;
              END IF;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_activation_regression BEFORE UPDATE ON release_activations "
            "FOR EACH ROW EXECUTE FUNCTION prevent_release_activation_regression()"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_acceptance_binding_consumption()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.provider IS DISTINCT FROM OLD.provider
                 OR NEW.subject_hmac IS DISTINCT FROM OLD.subject_hmac
                 OR NEW.environment IS DISTINCT FROM OLD.environment
                 OR NEW.deployment_id IS DISTINCT FROM OLD.deployment_id
                 OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                 OR NEW.actor IS DISTINCT FROM OLD.actor
                 OR NEW.reason IS DISTINCT FROM OLD.reason THEN
                RAISE EXCEPTION 'acceptance identity coordinates are immutable';
              END IF;
              IF OLD.consumed_at IS NOT NULL AND
                 (NEW.consumed_at IS DISTINCT FROM OLD.consumed_at OR
                  NEW.consumed_user_id IS DISTINCT FROM OLD.consumed_user_id) THEN
                RAISE EXCEPTION 'consumed acceptance identity is immutable';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_acceptance_binding_consumption BEFORE UPDATE ON acceptance_identity_bindings "
            "FOR EACH ROW EXECUTE FUNCTION enforce_acceptance_binding_consumption()"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_data_migration_run_fence()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.parent_run_id IS DISTINCT FROM OLD.parent_run_id
                 OR NEW.release_activation_id IS DISTINCT FROM OLD.release_activation_id
                 OR NEW.environment IS DISTINCT FROM OLD.environment
                 OR NEW.runtime_bundle_id IS DISTINCT FROM OLD.runtime_bundle_id
                 OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
                 OR NEW.inventory_sha256 IS DISTINCT FROM OLD.inventory_sha256
                 OR NEW.script_sha256 IS DISTINCT FROM OLD.script_sha256
                 OR NEW.mode IS DISTINCT FROM OLD.mode
                 OR NEW.approval IS DISTINCT FROM OLD.approval THEN
                RAISE EXCEPTION 'data migration run contract is immutable';
              END IF;
              IF NEW.fencing_token < OLD.fencing_token THEN
                RAISE EXCEPTION 'data migration fencing token cannot regress';
              END IF;
              IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
                 AND NEW.fencing_token <= OLD.fencing_token THEN
                RAISE EXCEPTION 'lease takeover requires a higher fencing token';
              END IF;
              IF OLD.state IN ('COMPLETED', 'CLEANED') THEN
                RAISE EXCEPTION 'terminal data migration run is immutable';
              END IF;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_data_migration_run_fence BEFORE UPDATE ON data_migration_runs "
            "FOR EACH ROW EXECUTE FUNCTION enforce_data_migration_run_fence()"
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_release_observation_cas()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.state IN ('PASSED', 'FAILED') THEN
                RAISE EXCEPTION 'terminal observation is immutable';
              END IF;
              IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'release observation requires version CAS';
              END IF;
              IF NOT (
                (OLD.state = 'OBSERVING' AND NEW.state IN ('OBSERVING', 'FINALIZING', 'FAILED')) OR
                (OLD.state = 'FINALIZING' AND NEW.state IN ('FINALIZING', 'PASSED', 'FAILED'))
              ) THEN
                RAISE EXCEPTION 'invalid release observation transition';
              END IF;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_observation_cas BEFORE UPDATE ON release_observation_runs "
            "FOR EACH ROW EXECUTE FUNCTION enforce_release_observation_cas()"
        )
    )


def _enable_control_plane_rls() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_migration_owner') THEN
                CREATE ROLE vowpic_migration_owner
                  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
              ELSIF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'vowpic_migration_owner'
                  AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR
                       rolreplication OR rolbypassrls OR NOT rolinherit)
              ) THEN
                RAISE EXCEPTION 'existing vowpic_migration_owner role violates the NOBYPASSRLS owner contract';
              END IF;

              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_runtime') THEN
                CREATE ROLE vowpic_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
              ELSIF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'vowpic_runtime'
                  AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
              ) THEN
                RAISE EXCEPTION 'existing vowpic_runtime role violates the NOBYPASSRLS group contract';
              END IF;

              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vowpic_control_writer') THEN
                CREATE ROLE vowpic_control_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
              ELSIF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'vowpic_control_writer'
                  AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
              ) THEN
                RAISE EXCEPTION 'existing vowpic_control_writer role violates the NOBYPASSRLS group contract';
              END IF;
            END
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "GRANT USAGE ON SCHEMA public TO "
            "vowpic_migration_owner, vowpic_runtime, vowpic_control_writer"
        )
    )
    for table in CONTROL_PLANE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE {table} FROM PUBLIC"))
        op.execute(
            sa.text(
                f"REVOKE ALL ON TABLE {table} FROM "
                "vowpic_migration_owner, vowpic_runtime, vowpic_control_writer"
            )
        )
        op.execute(sa.text(f"GRANT SELECT ON TABLE {table} TO vowpic_runtime"))
        op.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO vowpic_control_writer"
            )
        )
        op.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} "
                "TO vowpic_migration_owner"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_runtime_select ON {table} FOR SELECT TO vowpic_runtime "
                "USING (true)"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_control_writer_all ON {table} FOR ALL TO vowpic_control_writer "
                "USING (true) WITH CHECK (true)"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_migration_owner_all ON {table} FOR ALL "
                "TO vowpic_migration_owner USING (true) WITH CHECK (true)"
            )
        )
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    EXECUTE 'REVOKE ALL ON TABLE {table} FROM anon';
                  END IF;
                  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    EXECUTE 'REVOKE ALL ON TABLE {table} FROM authenticated';
                  END IF;
                END
                $$
                """
            )
        )
    op.execute(
        sa.text(
            "GRANT UPDATE (consumed_at, consumed_user_id) "
            "ON TABLE acceptance_identity_bindings TO vowpic_runtime"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY acceptance_identity_bindings_runtime_consume "
            "ON acceptance_identity_bindings FOR UPDATE TO vowpic_runtime "
            "USING (consumed_at IS NULL) "
            "WITH CHECK (consumed_at IS NOT NULL AND consumed_user_id IS NOT NULL)"
        )
    )


def upgrade() -> None:
    _reconcile_legacy_baseline()
    _create_release_activations()
    _create_identity_and_flags()
    _create_migration_control_plane()
    _create_observation_control_plane()
    _install_database_guards()
    _enable_control_plane_rls()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS acceptance_identity_bindings_runtime_consume "
            "ON acceptance_identity_bindings"
        )
    )
    for table in CONTROL_PLANE_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_runtime_select ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_control_writer_all ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_migration_owner_all ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_release_observation_cas() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_data_migration_run_fence() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_acceptance_binding_consumption() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_release_activation_regression() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_feature_flag_cas() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_control_plane_mutation() CASCADE"))
    for table in CONTROL_PLANE_TABLES:
        op.drop_table(table)
    op.execute(sa.text("DROP INDEX IF EXISTS ux_credit_transactions_order_refund_once"))
