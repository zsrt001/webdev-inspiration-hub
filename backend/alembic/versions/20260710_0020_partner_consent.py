"""Add authenticated Partner Invite consent and withdrawal facts.

Revision ID: 20260710_0020
Revises: 20260710_0019
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0020"
down_revision = "20260710_0019"
branch_labels = None
depends_on = None


PARTNER_TABLES = (
    "partner_invites",
    "partner_invite_events",
    "partner_consent_cases",
)

# The application login inherits only vowpic_runtime. The current website
# backend receives an explicit, non-destructive surface through vowpic_runtime
# before COMMERCIAL_7A can start; no external generation process is required.
COMMERCIAL_7A_RUNTIME_ADDITIVE_PRIVILEGES = {
    "upload_batches": ("SELECT", "INSERT", "UPDATE"),
    "media_assets": ("SELECT", "INSERT", "UPDATE"),
    "asset_access_grants": ("SELECT", "INSERT", "UPDATE"),
    "upload_quota_windows": ("SELECT", "INSERT", "UPDATE"),
    "upload_quota_states": ("SELECT", "INSERT", "UPDATE"),
    "upload_quota_reservations": ("SELECT", "INSERT", "UPDATE"),
    "billing_catalog_versions": ("SELECT",),
    "billing_products": ("SELECT",),
    "billing_provider_products": ("SELECT",),
    "credit_grant_lots": ("SELECT", "INSERT", "UPDATE"),
    "credit_reservations": ("SELECT", "INSERT", "UPDATE"),
    "credit_reservation_allocations": ("SELECT", "INSERT"),
    "order_entitlements": ("SELECT", "INSERT", "UPDATE"),
    "order_entitlement_fundings": ("SELECT", "INSERT"),
    "welcome_grant_claims": ("SELECT", "INSERT"),
    "payment_reconciliation_cases": ("SELECT", "INSERT", "UPDATE"),
    "idempotency_records": ("SELECT", "INSERT", "UPDATE"),
    "outbox_events": ("SELECT", "INSERT", "UPDATE"),
    "payment_capture_facts": ("SELECT", "INSERT"),
    "payment_refund_facts": ("SELECT", "INSERT"),
    "payment_dispute_facts": ("SELECT", "INSERT"),
    "subscription_checkout_intents": ("SELECT", "INSERT", "UPDATE"),
    "subscription_invoices": ("SELECT", "INSERT", "UPDATE"),
    "subscription_invoice_adjustment_facts": ("SELECT", "INSERT"),
    "subscription_cancel_intents": ("SELECT", "INSERT", "UPDATE"),
    "generation_jobs": ("SELECT", "INSERT", "UPDATE"),
    "generation_attempts": ("SELECT", "INSERT", "UPDATE"),
    "qa_verdicts": ("SELECT", "INSERT"),
    "partner_invites": ("SELECT", "INSERT", "UPDATE"),
    "partner_invite_events": ("SELECT", "INSERT"),
    "partner_consent_cases": ("SELECT", "INSERT", "UPDATE"),
}
RUNTIME_POLICY_COMMANDS = ("SELECT", "INSERT", "UPDATE")


def _create_release_phase_evidence() -> None:
    op.create_table(
        "release_phase_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "release_activation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_activations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("phase_rank", sa.Integer(), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("private_object_key", sa.String(512), nullable=False),
        sa.Column("coordinates_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "release_activation_id",
            "phase",
            name="uq_release_phase_evidence_phase",
        ),
        sa.UniqueConstraint(
            "release_activation_id",
            "phase_rank",
            name="uq_release_phase_evidence_rank",
        ),
        sa.CheckConstraint("phase_rank > 0", name="ck_release_phase_evidence_rank"),
        sa.CheckConstraint(
            "report_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_release_phase_evidence_report_sha",
        ),
        sa.CheckConstraint(
            "private_object_key !~ '(^|/)\\.\\.?(/|$)'",
            name="ck_release_phase_evidence_object_key",
        ),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_phase_evidence_append_only "
            "BEFORE UPDATE OR DELETE ON public.release_phase_evidence "
            "FOR EACH ROW EXECUTE FUNCTION prevent_control_plane_mutation()"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.release_phase_evidence ENABLE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.release_phase_evidence FORCE ROW LEVEL SECURITY"
        )
    )
    op.execute(sa.text("REVOKE ALL ON TABLE public.release_phase_evidence FROM PUBLIC"))
    for role in ("vowpic_control_writer", "vowpic_migration_owner"):
        if _role_exists(role):
            op.execute(
                sa.text(
                    f"GRANT SELECT, INSERT ON TABLE public.release_phase_evidence TO {role}"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE POLICY release_phase_evidence_{role}_all "
                    "ON public.release_phase_evidence FOR ALL "
                    f"TO {role} USING (true) WITH CHECK (true)"
                )
            )


def _create_release_observation_recoveries() -> None:
    op.create_table(
        "release_observation_recoveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "observation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_observation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("resolution_sha256", sa.String(64), nullable=False),
        sa.Column("worker_report_sha256", sa.String(64), nullable=False),
        sa.Column("api_report_sha256", sa.String(64), nullable=False),
        sa.Column("approval_sha256", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(64), nullable=False),
        sa.Column("recovery_report_sha256", sa.String(64), nullable=False),
        sa.Column("private_object_key", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "observation_run_id",
            name="uq_release_observation_recovery_run",
        ),
        sa.CheckConstraint(
            "disposition = 'ROLLED_BACK_PRIVATE_BASELINE'",
            name="ck_release_observation_recovery_disposition",
        ),
        *(
            sa.CheckConstraint(
                f"{column} ~ '^[0-9a-f]{{64}}$'",
                name=f"ck_release_observation_recovery_{column.removesuffix('_sha256')}_sha",
            )
            for column in (
                "resolution_sha256",
                "worker_report_sha256",
                "api_report_sha256",
                "approval_sha256",
                "recovery_report_sha256",
            )
        ),
        sa.CheckConstraint(
            "private_object_key !~ '(^|/)\\.\\.?(/|$)'",
            name="ck_release_observation_recovery_object_key",
        ),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_observation_recovery_append_only "
            "BEFORE UPDATE OR DELETE ON public.release_observation_recoveries "
            "FOR EACH ROW EXECUTE FUNCTION prevent_control_plane_mutation()"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.release_observation_recoveries "
            "ENABLE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.release_observation_recoveries "
            "FORCE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON TABLE public.release_observation_recoveries FROM PUBLIC"
        )
    )
    for role in ("vowpic_control_writer", "vowpic_migration_owner"):
        if _role_exists(role):
            op.execute(
                sa.text(
                    "GRANT SELECT, INSERT ON TABLE "
                    f"public.release_observation_recoveries TO {role}"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE POLICY release_observation_recoveries_{role}_all "
                    "ON public.release_observation_recoveries FOR ALL "
                    f"TO {role} USING (true) WITH CHECK (true)"
                )
            )


def _create_observation_database_contract() -> None:
    for role in ("vowpic_observation_reader", "vowpic_observation_writer"):
        if not _role_exists(role):
            op.execute(
                sa.text(
                    f"CREATE ROLE {role} NOLOGIN NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
                )
            )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM pg_roles AS role
                WHERE role.rolname IN (
                    'vowpic_observation_reader',
                    'vowpic_observation_writer'
                )
                  AND (
                    role.rolcanlogin OR role.rolsuper OR role.rolcreatedb OR
                    role.rolcreaterole OR role.rolreplication OR
                    role.rolbypassrls OR NOT role.rolinherit
                  )
              ) THEN
                RAISE EXCEPTION
                  'an observation database group violates the NOLOGIN/NOBYPASSRLS contract';
              END IF;
              IF EXISTS (
                SELECT 1
                FROM pg_auth_members AS membership
                JOIN pg_roles AS member ON member.oid = membership.member
                WHERE member.rolname IN (
                    'vowpic_observation_reader',
                    'vowpic_observation_writer'
                )
              ) THEN
                RAISE EXCEPTION
                  'an observation database group has an unexpected membership';
              END IF;
              IF EXISTS (
                SELECT 1
                FROM pg_roles AS role
                WHERE role.rolname IN (
                    'vowpic_observation_reader',
                    'vowpic_observation_writer'
                )
                  AND (
                    EXISTS (
                      SELECT 1 FROM pg_database AS database
                      WHERE database.datdba = role.oid
                    ) OR EXISTS (
                      SELECT 1 FROM pg_namespace AS namespace
                      WHERE namespace.nspowner = role.oid
                    ) OR EXISTS (
                      SELECT 1 FROM pg_class AS relation
                      WHERE relation.relowner = role.oid
                    ) OR EXISTS (
                      SELECT 1 FROM pg_proc AS routine
                      WHERE routine.proowner = role.oid
                    )
                  )
              ) THEN
                RAISE EXCEPTION
                  'an observation database group owns database objects';
              END IF;
            END
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "GRANT USAGE ON SCHEMA public TO "
            "vowpic_observation_reader, vowpic_observation_writer"
        )
    )
    for table in (
        "release_activations",
        "release_observation_runs",
        "release_observation_samples",
    ):
        op.execute(
            sa.text(
                f"GRANT SELECT ON TABLE public.{table} TO "
                "vowpic_observation_reader, vowpic_observation_writer"
            )
        )
        for role in ("vowpic_observation_reader", "vowpic_observation_writer"):
            op.execute(
                sa.text(
                    f"CREATE POLICY {table}_{role}_select "
                    f"ON public.{table} FOR SELECT TO {role} USING (true)"
                )
            )
    op.execute(
        sa.text(
            "GRANT INSERT ON TABLE public.release_observation_samples "
            "TO vowpic_observation_writer"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY release_observation_samples_observation_writer_insert "
            "ON public.release_observation_samples FOR INSERT "
            "TO vowpic_observation_writer WITH CHECK (true)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.read_release_observation_metrics_v1(
                p_observation_run_id uuid
            )
            RETURNS TABLE (
                unhandled_signed_webhooks bigint,
                ledger_reconciliation_failures bigint,
                oldest_mandatory_outbox_age_seconds bigint,
                synthetic_flow_dlq bigint,
                acceptance_prefix_deletion_failures bigint,
                rls_policy_gap_count bigint,
                legacy_identity_fallback_count bigint,
                flag_bundle_drift bigint
            )
            LANGUAGE sql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            WITH exact_run AS (
                SELECT observation.id, observation.started_at,
                       activation.id AS activation_id,
                       activation.api_deployment_id,
                       activation.runtime_bundle_id,
                       activation.worker_image_digest,
                       activation.manifest_sha256
                FROM public.release_observation_runs AS observation
                JOIN public.release_activations AS activation
                  ON activation.id = observation.release_activation_id
                WHERE observation.id = p_observation_run_id
                  AND observation.state IN ('OBSERVING', 'FINALIZING')
                  AND activation.environment = 'production'
                  AND activation.kind = 'COMMERCIAL_7A'
                  AND activation.phase = 'OBSERVING'
            ),
            required_capabilities(capability) AS (
                VALUES
                    ('google_auth'),
                    ('authenticated_upload'),
                    ('credit_pack_checkout'),
                    ('subscription_billing'),
                    ('generation'),
                    ('private_download'),
                    ('partner_invite')
            ),
            required_rls_tables(table_name) AS (
                VALUES
                    ('users'),
                    ('user_identities'),
                    ('auth_sessions'),
                    ('auth_refresh_tokens'),
                    ('media_assets'),
                    ('asset_access_grants'),
                    ('credit_reservations'),
                    ('order_entitlements'),
                    ('payment_reconciliation_cases'),
                    ('outbox_events'),
                    ('generation_jobs'),
                    ('generation_attempts'),
                    ('qa_verdicts'),
                    ('partner_invites'),
                    ('partner_consent_cases'),
                    ('ops_feature_flags'),
                    ('release_activations'),
                    ('release_observation_runs'),
                    ('release_observation_samples'),
                    ('release_observation_recoveries')
            )
            SELECT
                (
                    SELECT count(*)
                    FROM public.payment_events
                    WHERE processing_state IN (
                        'UNHANDLED', 'RECONCILIATION_REQUIRED'
                    )
                ),
                (
                    SELECT count(*)
                    FROM public.payment_reconciliation_cases
                    WHERE status IN ('OPEN', 'ESCALATED')
                ),
                COALESCE((
                    SELECT greatest(
                        0,
                        floor(extract(epoch FROM (
                            clock_timestamp() - min(created_at)
                        )))::bigint
                    )
                    FROM public.outbox_events
                    WHERE status IN ('PENDING', 'PROCESSING', 'FAILED')
                ), 0),
                (
                    SELECT count(*)
                    FROM public.generation_jobs AS job
                    JOIN public.orders AS customer_order
                      ON customer_order.id = job.order_id
                    CROSS JOIN exact_run
                    WHERE job.status = 'FAILED'
                      AND job.created_at >= exact_run.started_at
                      AND job.api_deployment_id = exact_run.api_deployment_id
                      AND job.runtime_bundle_id = exact_run.runtime_bundle_id
                      AND EXISTS (
                          SELECT 1
                          FROM public.acceptance_identity_bindings AS binding
                          WHERE binding.environment = 'production'
                            AND binding.deployment_id =
                                exact_run.api_deployment_id
                            AND binding.consumed_user_id =
                                customer_order.user_id
                      )
                ),
                (
                    SELECT count(*)
                    FROM public.media_assets AS asset
                    CROSS JOIN exact_run
                    WHERE asset.status = 'DELETE_FAILED'
                      AND asset.created_at >= exact_run.started_at
                      AND EXISTS (
                          SELECT 1
                          FROM public.acceptance_identity_bindings AS binding
                          WHERE binding.environment = 'production'
                            AND binding.deployment_id =
                                exact_run.api_deployment_id
                            AND binding.consumed_user_id = asset.owner_user_id
                      )
                ),
                (
                    SELECT count(*)
                    FROM required_rls_tables AS required
                    LEFT JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.nspname = 'public'
                    LEFT JOIN pg_catalog.pg_class AS relation
                      ON relation.relnamespace = namespace.oid
                     AND relation.relname = required.table_name
                     AND relation.relkind = 'r'
                    WHERE relation.oid IS NULL
                       OR relation.relrowsecurity IS NOT TRUE
                       OR NOT EXISTS (
                           SELECT 1
                           FROM pg_catalog.pg_policy AS policy
                           WHERE policy.polrelid = relation.oid
                       )
                ),
                (
                    SELECT CASE WHEN sequence_state.is_called
                                THEN sequence_state.last_value ELSE 0 END
                    FROM public.identity_legacy_fallback_uses_seq
                         AS sequence_state
                ),
                (
                    SELECT count(*)
                    FROM required_capabilities AS required
                    CROSS JOIN exact_run
                    LEFT JOIN public.ops_feature_flags AS flag
                      ON flag.environment = 'production'
                     AND flag.capability = required.capability
                    WHERE flag.id IS NULL
                       OR flag.state <> 'ON'
                       OR flag.deployment_id IS DISTINCT FROM
                            exact_run.api_deployment_id
                       OR flag.runtime_bundle_id IS DISTINCT FROM
                            exact_run.runtime_bundle_id
                       OR flag.worker_image_digest IS DISTINCT FROM
                            exact_run.worker_image_digest
                       OR flag.release_activation_id IS DISTINCT FROM
                            exact_run.activation_id
                       OR flag.target_manifest_sha256 IS DISTINCT FROM
                            exact_run.manifest_sha256
                       OR flag.cohort_user_ids <> '[]'::jsonb
                       OR flag.verified_identity_hashes <> '[]'::jsonb
                       OR flag.expires_at IS NOT NULL
                )
            FROM exact_run
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION "
            "public.read_release_observation_metrics_v1(uuid) FROM PUBLIC"
        )
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION "
            "public.read_release_observation_metrics_v1(uuid) "
            "TO vowpic_observation_reader"
        )
    )


def _create_release_auth_origin_leases() -> None:
    op.create_table(
        "release_auth_origin_leases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "release_activation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_activations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("project_ref_sha256", sa.String(64), nullable=False),
        sa.Column("callback_url", sa.String(512), nullable=False),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("target_sha256", sa.String(64), nullable=False),
        sa.Column("private_object_key", sa.String(512), nullable=False),
        sa.Column("approval", sa.String(160), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="SNAPSHOTTED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "release_activation_id", name="uq_release_auth_origin_lease_activation"
        ),
        sa.UniqueConstraint("callback_url", name="uq_release_auth_origin_lease_callback"),
        sa.CheckConstraint(
            "state IN ('SNAPSHOTTED','ADDED','REMOVED')",
            name="ck_release_auth_origin_lease_state",
        ),
        sa.CheckConstraint("version > 0", name="ck_release_auth_origin_lease_version"),
        sa.CheckConstraint(
            "project_ref_sha256 ~ '^[0-9a-f]{64}$' AND "
            "original_sha256 ~ '^[0-9a-f]{64}$' AND "
            "target_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_release_auth_origin_lease_hashes",
        ),
        sa.CheckConstraint(
            "original_sha256 <> target_sha256",
            name="ck_release_auth_origin_lease_distinct_snapshots",
        ),
        sa.CheckConstraint(
            "private_object_key !~ '(^|/)\\.\\.?(/|$)'",
            name="ck_release_auth_origin_lease_object_key",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + INTERVAL '24 hours'",
            name="ck_release_auth_origin_lease_ttl",
        ),
        sa.CheckConstraint(
            "(state = 'REMOVED' AND removed_at IS NOT NULL) OR "
            "(state <> 'REMOVED' AND removed_at IS NULL)",
            name="ck_release_auth_origin_lease_removed_at",
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.enforce_release_auth_origin_lease()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $release_auth_origin_lease$
            BEGIN
              IF NEW.release_activation_id IS DISTINCT FROM OLD.release_activation_id
                 OR NEW.project_ref_sha256 IS DISTINCT FROM OLD.project_ref_sha256
                 OR NEW.callback_url IS DISTINCT FROM OLD.callback_url
                 OR NEW.original_sha256 IS DISTINCT FROM OLD.original_sha256
                 OR NEW.target_sha256 IS DISTINCT FROM OLD.target_sha256
                 OR NEW.private_object_key IS DISTINCT FROM OLD.private_object_key
                 OR NEW.approval IS DISTINCT FROM OLD.approval
                 OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                 OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'release auth origin lease coordinates are immutable';
              END IF;
              IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'release auth origin lease requires version CAS';
              END IF;
              IF NOT (
                (OLD.state = 'SNAPSHOTTED' AND NEW.state IN ('SNAPSHOTTED','ADDED','REMOVED')) OR
                (OLD.state = 'ADDED' AND NEW.state IN ('ADDED','REMOVED')) OR
                (OLD.state = 'REMOVED' AND NEW.state = 'REMOVED')
              ) THEN
                RAISE EXCEPTION 'invalid release auth origin lease transition';
              END IF;
              IF OLD.state = 'REMOVED' THEN
                RAISE EXCEPTION 'removed release auth origin lease is immutable';
              END IF;
              NEW.updated_at := CURRENT_TIMESTAMP;
              RETURN NEW;
            END;
            $release_auth_origin_lease$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_auth_origin_lease_cas "
            "BEFORE UPDATE ON public.release_auth_origin_leases "
            "FOR EACH ROW EXECUTE FUNCTION public.enforce_release_auth_origin_lease()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_release_auth_origin_lease_no_delete "
            "BEFORE DELETE ON public.release_auth_origin_leases "
            "FOR EACH ROW EXECUTE FUNCTION prevent_control_plane_mutation()"
        )
    )
    op.execute(sa.text("ALTER TABLE public.release_auth_origin_leases ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE public.release_auth_origin_leases FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("REVOKE ALL ON TABLE public.release_auth_origin_leases FROM PUBLIC"))
    for role in ("vowpic_control_writer", "vowpic_migration_owner"):
        if _role_exists(role):
            op.execute(
                sa.text(
                    f"GRANT SELECT, INSERT, UPDATE ON TABLE public.release_auth_origin_leases TO {role}"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE POLICY release_auth_origin_leases_{role}_all "
                    "ON public.release_auth_origin_leases FOR ALL "
                    f"TO {role} USING (true) WITH CHECK (true)"
                )
            )


def _role_exists(role_name: str) -> bool:
    return bool(
        op.get_bind().execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": role_name},
        ).scalar()
    )


def _create_service_role() -> None:
    # service_role authority is isolated from browser-authenticated identities.
    role_name = "vowpic_partner_service"
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = :role"
        ),
        {"role": role_name},
    ).mappings().one_or_none()
    if existing is not None:
        if any(bool(value) for value in existing.values()):
            raise RuntimeError("unsafe pre-existing Partner service role")
        return
    op.execute(
        sa.text(
            "CREATE ROLE vowpic_partner_service NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
    )


def _create_tables() -> None:
    op.create_table(
        "partner_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("host_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("host_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("partner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("partner_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="COUPLE"),
        sa.Column("order_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_intent_hash", sa.String(64), nullable=False),
        sa.Column("intent_policy_version", sa.String(64), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATED"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("partner_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("partner_asset_sha256", sa.String(64), nullable=True),
        sa.Column("consent_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["host_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["host_identity_id"], ["user_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_identity_id"], ["user_identities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_partner_invites_token_hash"),
        sa.UniqueConstraint("order_intent_id", name="uq_partner_invites_order_intent_id"),
        sa.UniqueConstraint("order_id", name="uq_partner_invites_order_id"),
        sa.UniqueConstraint("job_id", name="uq_partner_invites_job_id"),
        sa.CheckConstraint(
            "status IN ('CREATED','ACCEPTED','CONSENTED','COMPLETED','REVOKED','EXPIRED','CANCELLED')",
            name="ck_partner_invites_status",
        ),
        sa.CheckConstraint("purpose = 'COUPLE'", name="ck_partner_invites_purpose"),
        sa.CheckConstraint("char_length(token_hash) = 64", name="ck_partner_invites_token_hash"),
        sa.CheckConstraint("char_length(order_intent_hash) = 64", name="ck_partner_invites_intent_hash"),
        sa.CheckConstraint("btrim(template_id) <> ''", name="ck_partner_invites_template_nonempty"),
        sa.CheckConstraint("version >= 1", name="ck_partner_invites_version"),
        sa.CheckConstraint(
            "partner_user_id IS NULL OR (partner_user_id <> host_user_id "
            "AND partner_identity_id <> host_identity_id)",
            name="ck_partner_invites_identity_distinct",
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '1 day'",
            name="ck_partner_invites_expiry_exact",
        ),
        sa.CheckConstraint(
            "(partner_user_id IS NULL) = (partner_identity_id IS NULL)",
            name="ck_partner_invites_partner_binding_coherent",
        ),
        sa.CheckConstraint(
            "(partner_asset_id IS NULL) = (partner_asset_sha256 IS NULL) "
            "AND (partner_asset_id IS NULL) = (consent_event_id IS NULL)",
            name="ck_partner_invites_consent_asset_coherent",
        ),
        sa.CheckConstraint(
            "(order_id IS NULL) = (job_id IS NULL)",
            name="ck_partner_invites_order_binding_coherent",
        ),
    )
    for column in ("host_user_id", "partner_user_id", "status", "expires_at"):
        op.create_index(f"ix_partner_invites_{column}", "partner_invites", [column])

    op.create_table(
        "partner_invite_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("invite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("command", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=False),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("invite_version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invite_id"], ["partner_invites.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("invite_id", "invite_version", name="uq_partner_invite_events_version"),
        sa.UniqueConstraint("request_id", name="uq_partner_invite_events_request_id"),
    )
    op.create_index("ix_partner_invite_events_invite_id", "partner_invite_events", ["invite_id"])
    op.create_foreign_key(
        "fk_partner_invites_consent_event",
        "partner_invites",
        "partner_invite_events",
        ["consent_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "partner_consent_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("invite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("host_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("partner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("owned_asset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provider_cancel_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settlement_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invite_id"], ["partner_invites.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["host_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("invite_id", name="uq_partner_consent_cases_invite"),
        sa.CheckConstraint(
            "status IN ('OPEN','SETTLED_DELETION_PENDING','CANCELLED_AND_DELETED')",
            name="ck_partner_consent_cases_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_partner_consent_cases_version"),
        sa.CheckConstraint(
            "owned_asset_ids IS NULL OR jsonb_typeof(owned_asset_ids) = 'array'",
            name="ck_partner_consent_cases_asset_ids_array",
        ),
    )
    for column in ("host_user_id", "partner_user_id", "order_id", "job_id", "status"):
        op.create_index(f"ix_partner_consent_cases_{column}", "partner_consent_cases", [column])


def _create_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_partner_invite_transition()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $partner_invite_guard$
            DECLARE
                terminal_rebind boolean;
            BEGIN
                terminal_rebind := (
                    NEW.status = OLD.status
                    AND OLD.status IN ('COMPLETED','REVOKED','EXPIRED','CANCELLED')
                    AND (
                        NEW.host_user_id IS DISTINCT FROM OLD.host_user_id
                        OR NEW.partner_user_id IS DISTINCT FROM OLD.partner_user_id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM public.partner_consent_cases AS consent_case
                        WHERE consent_case.invite_id = OLD.id
                          AND consent_case.status IN ('OPEN','SETTLED_DELETION_PENDING')
                    )
                );
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR (
                       NEW.host_user_id IS DISTINCT FROM OLD.host_user_id
                       AND NOT terminal_rebind
                   )
                   OR NEW.host_identity_id IS DISTINCT FROM OLD.host_identity_id
                   OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
                   OR NEW.purpose IS DISTINCT FROM OLD.purpose
                   OR NEW.order_intent_id IS DISTINCT FROM OLD.order_intent_id
                   OR NEW.order_intent_hash IS DISTINCT FROM OLD.order_intent_hash
                   OR NEW.intent_policy_version IS DISTINCT FROM OLD.intent_policy_version
                   OR NEW.template_id IS DISTINCT FROM OLD.template_id
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'immutable Partner Invite facts cannot change' USING ERRCODE = '23514';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Partner Invite version must increment exactly once' USING ERRCODE = '23514';
                END IF;
                IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                    (OLD.status = 'CREATED' AND NEW.status IN ('ACCEPTED','REVOKED','EXPIRED'))
                    OR (OLD.status = 'ACCEPTED' AND NEW.status IN ('CONSENTED','CANCELLED','REVOKED','EXPIRED'))
                    OR (OLD.status = 'CONSENTED' AND NEW.status IN ('COMPLETED','CANCELLED','REVOKED','EXPIRED'))
                ) THEN
                    RAISE EXCEPTION 'invalid Partner Invite transition' USING ERRCODE = '23514';
                END IF;
                IF OLD.partner_user_id IS NOT NULL AND (
                    (
                        NEW.partner_user_id IS DISTINCT FROM OLD.partner_user_id
                        AND NOT terminal_rebind
                    )
                    OR NEW.partner_identity_id IS DISTINCT FROM OLD.partner_identity_id
                ) THEN
                    RAISE EXCEPTION 'Partner binding is immutable' USING ERRCODE = '23514';
                END IF;
                IF OLD.consent_event_id IS NOT NULL AND (
                    NEW.consent_event_id IS DISTINCT FROM OLD.consent_event_id
                    OR NEW.partner_asset_id IS DISTINCT FROM OLD.partner_asset_id
                    OR NEW.partner_asset_sha256 IS DISTINCT FROM OLD.partner_asset_sha256
                ) THEN
                    RAISE EXCEPTION 'Partner consent is immutable' USING ERRCODE = '23514';
                END IF;
                IF OLD.order_id IS NOT NULL AND (
                    NEW.order_id IS DISTINCT FROM OLD.order_id OR NEW.job_id IS DISTINCT FROM OLD.job_id
                ) THEN
                    RAISE EXCEPTION 'Partner order binding is immutable' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $partner_invite_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_partner_invites_guard BEFORE UPDATE ON public.partner_invites "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_partner_invite_transition()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_partner_consent_case_transition()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $partner_case_guard$
            DECLARE
                terminal_rebind boolean;
            BEGIN
                -- OPEN -> SETTLED_DELETION_PENDING
                -- SETTLED_DELETION_PENDING -> CANCELLED_AND_DELETED
                -- terminal_rebind is allowed only after settlement and deletion close.
                terminal_rebind := (
                    OLD.status = 'CANCELLED_AND_DELETED'
                    AND NEW.status = OLD.status
                    AND (
                        NEW.host_user_id IS DISTINCT FROM OLD.host_user_id
                        OR NEW.partner_user_id IS DISTINCT FROM OLD.partner_user_id
                    )
                );
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.invite_id IS DISTINCT FROM OLD.invite_id
                   OR (
                       NEW.host_user_id IS DISTINCT FROM OLD.host_user_id
                       AND NOT terminal_rebind
                   )
                   OR (
                       NEW.partner_user_id IS DISTINCT FROM OLD.partner_user_id
                       AND NOT terminal_rebind
                   )
                   OR NEW.order_id IS DISTINCT FROM OLD.order_id OR NEW.job_id IS DISTINCT FROM OLD.job_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'immutable Partner consent case facts cannot change' USING ERRCODE = '23514';
                END IF;
                IF OLD.status IN ('OPEN','SETTLED_DELETION_PENDING') AND terminal_rebind THEN
                    RAISE EXCEPTION 'partner_consent_case_nonterminal' USING ERRCODE = '23514';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Partner consent case version must increment exactly once' USING ERRCODE = '23514';
                END IF;
                IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                    (OLD.status = 'OPEN' AND NEW.status = 'SETTLED_DELETION_PENDING')
                    OR (OLD.status = 'SETTLED_DELETION_PENDING' AND NEW.status = 'CANCELLED_AND_DELETED')
                ) THEN
                    RAISE EXCEPTION 'invalid Partner consent case transition' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $partner_case_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_partner_consent_cases_guard BEFORE UPDATE ON public.partner_consent_cases "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_partner_consent_case_transition()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.reject_partner_audit_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $partner_audit_guard$
            BEGIN
                RAISE EXCEPTION 'Partner Invite events are append-only' USING ERRCODE = '23514';
            END;
            $partner_audit_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_partner_invite_events_append_only BEFORE UPDATE OR DELETE "
            "ON public.partner_invite_events FOR EACH ROW EXECUTE FUNCTION public.reject_partner_audit_mutation()"
        )
    )


def _create_rls() -> None:
    _create_service_role()
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO vowpic_partner_service"))
    for table in PARTNER_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))
        for role in ("authenticated", "vowpic_runtime", "vowpic_control_writer"):
            if _role_exists(role):
                op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM {role}"))
        op.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table} TO vowpic_partner_service"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_partner_service_all ON public.{table} "
                "FOR ALL TO vowpic_partner_service USING (true) WITH CHECK (true)"
            )
        )
    if _role_exists("authenticated"):
        op.execute(
            sa.text(
                "GRANT SELECT (id, host_user_id, partner_user_id, purpose, order_intent_id, "
                "intent_policy_version, template_id, status, expires_at, accepted_at, consented_at, "
                "completed_at, revoked_at, cancelled_at, version, created_at, updated_at) "
                "ON TABLE public.partner_invites TO authenticated"
            )
        )
        op.execute(
            sa.text(
                "CREATE POLICY partner_invites_authenticated_participant_select "
                "ON public.partner_invites FOR SELECT TO authenticated USING ("
                "host_user_id = public.app_current_user_id() "
                "OR partner_user_id = public.app_current_user_id())"
            )
        )


def _configure_commercial_7a_runtime_surface() -> None:
    if not _role_exists("vowpic_runtime"):
        raise RuntimeError("vowpic_runtime role is missing before COMMERCIAL_7A")

    for table, privileges in COMMERCIAL_7A_RUNTIME_ADDITIVE_PRIVILEGES.items():
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM vowpic_runtime"))
        if _role_exists("vowpic_control_writer"):
            op.execute(
                sa.text(
                    f"REVOKE ALL ON TABLE public.{table} FROM vowpic_control_writer"
                )
            )
        op.execute(
            sa.text(
                f"GRANT {', '.join(privileges)} ON TABLE public.{table} "
                "TO vowpic_runtime"
            )
        )
        for command in RUNTIME_POLICY_COMMANDS:
            op.execute(
                sa.text(
                    f"DROP POLICY IF EXISTS "
                    f"{table}_vowpic_runtime_{command.lower()} "
                    f"ON public.{table}"
                )
            )
        for command in privileges:
            policy = f"{table}_vowpic_runtime_{command.lower()}"
            if command == "SELECT":
                clause = "USING (true)"
            elif command == "INSERT":
                clause = "WITH CHECK (true)"
            elif command == "UPDATE":
                clause = "USING (true) WITH CHECK (true)"
            else:  # pragma: no cover - the committed contract is exact.
                raise RuntimeError(
                    f"unsupported COMMERCIAL_7A runtime privilege: {command}"
                )
            op.execute(
                sa.text(
                    f"CREATE POLICY {policy} ON public.{table} "
                    f"FOR {command} TO vowpic_runtime {clause}"
                )
            )


def upgrade() -> None:
    _create_release_phase_evidence()
    _create_release_observation_recoveries()
    _create_observation_database_contract()
    _create_release_auth_origin_leases()
    _create_tables()
    _create_guards()
    _create_rls()
    _configure_commercial_7a_runtime_surface()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS "
            "public.read_release_observation_metrics_v1(uuid)"
        )
    )
    for table in (
        "release_activations",
        "release_observation_runs",
        "release_observation_samples",
    ):
        for role in ("vowpic_observation_reader", "vowpic_observation_writer"):
            op.execute(
                sa.text(
                    f"DROP POLICY IF EXISTS {table}_{role}_select "
                    f"ON public.{table}"
                )
            )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS "
            "release_observation_samples_observation_writer_insert "
            "ON public.release_observation_samples"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_invite_events_append_only ON public.partner_invite_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.reject_partner_audit_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_consent_cases_guard ON public.partner_consent_cases"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_partner_consent_case_transition()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_invites_guard ON public.partner_invites"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_partner_invite_transition()"))
    op.drop_constraint("fk_partner_invites_consent_event", "partner_invites", type_="foreignkey")
    for table in reversed(PARTNER_TABLES):
        op.drop_table(table)
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_release_observation_recovery_append_only "
            "ON public.release_observation_recoveries"
        )
    )
    op.drop_table("release_observation_recoveries")
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_release_auth_origin_lease_no_delete "
            "ON public.release_auth_origin_leases"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_release_auth_origin_lease_cas "
            "ON public.release_auth_origin_leases"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.enforce_release_auth_origin_lease()"))
    op.drop_table("release_auth_origin_leases")
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_release_phase_evidence_append_only "
            "ON public.release_phase_evidence"
        )
    )
    op.drop_table("release_phase_evidence")
    if _role_exists("vowpic_partner_service"):
        op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM vowpic_partner_service"))
