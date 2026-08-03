"""Add durable generation jobs, attempts, QA facts, and atomic order links.

Revision ID: 20260710_0019
Revises: 20260710_0018
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0019"
down_revision = "20260710_0018"
branch_labels = None
depends_on = None


GENERATION_TABLES = ("generation_jobs", "generation_attempts", "qa_verdicts")


def _create_generation_role() -> None:
    role_name = "vowpic_generation_service"
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
            raise RuntimeError("unsafe pre-existing generation service role")
        return
    op.execute(
        sa.text(
            "CREATE ROLE vowpic_generation_service NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
    )


def _create_generation_jobs() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), server_default="QUEUED", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("repair_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("payload_version", sa.String(32), nullable=False),
        sa.Column("api_deployment_id", sa.String(128), nullable=False),
        sa.Column("runtime_bundle_id", sa.String(128), nullable=False),
        sa.Column("expected_worker_image_digest", sa.String(128), nullable=False),
        sa.Column("settlement_status", sa.String(32), server_default="RESERVED", nullable=False),
        sa.Column("delivery_status", sa.String(32), server_default="PENDING", nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "submission_correlation_id",
            name="uq_generation_jobs_submission_correlation",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','ACTIVE','RECONCILING','FINISHED','FAILED','CANCELLED')",
            name="ck_generation_jobs_status",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND repair_count >= 0 AND fencing_token >= 0",
            name="ck_generation_jobs_counters",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_generation_jobs_lease_coherent",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR fencing_token > 0",
            name="ck_generation_jobs_active_lease_fenced",
        ),
        sa.CheckConstraint(
            "heartbeat_at IS NULL OR lease_owner IS NOT NULL",
            name="ck_generation_jobs_heartbeat_has_lease",
        ),
        sa.CheckConstraint(
            "payload_version <> 'generation-job.v1' OR "
            "(submission_correlation_id IS NOT NULL AND btrim(api_deployment_id) <> '' "
            "AND btrim(runtime_bundle_id) <> '' AND btrim(expected_worker_image_digest) <> '')",
            name="ck_generation_jobs_v1_runtime_stamps",
        ),
    )
    for column in (
        "order_id",
        "submission_correlation_id",
        "status",
        "next_retry_at",
        "lease_expires_at",
    ):
        op.create_index(f"ix_generation_jobs_{column}", "generation_jobs", [column])
    op.create_index(
        "uq_generation_jobs_nonterminal_order",
        "generation_jobs",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','ACTIVE','RECONCILING')"),
    )
    op.create_index(
        "uq_generation_jobs_active_lease_claim",
        "generation_jobs",
        ["lease_claim_id"],
        unique=True,
        postgresql_where=sa.text("lease_claim_id IS NOT NULL"),
    )


def _create_generation_attempts() -> None:
    op.create_table(
        "generation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="PREPARED", nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("provider_job_id", sa.String(128), nullable=True),
        sa.Column("source_verdict_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("submission_accounting_state", sa.String(32), server_default="NOT_CAPTURED", nullable=False),
        sa.Column("request_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("result_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cost_minor_units", sa.Integer(), nullable=True),
        sa.Column("cost_currency", sa.String(3), nullable=True),
        sa.Column("submit_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_generation_attempt_number"),
        sa.UniqueConstraint("provider", "client_request_id", name="uq_generation_attempt_client_request"),
        sa.UniqueConstraint("provider", "provider_job_id", name="uq_generation_attempt_provider_job"),
        sa.UniqueConstraint("source_verdict_id", name="uq_generation_attempt_source_verdict"),
        sa.CheckConstraint("attempt_number > 0", name="ck_generation_attempt_number_positive"),
        sa.CheckConstraint("kind IN ('INITIAL','REPAIR','INFRA_RETRY')", name="ck_generation_attempt_kind"),
        sa.CheckConstraint(
            "status IN ('PREPARED','SUBMITTING','SUBMITTED','UNKNOWN','FINISHED','FAILED')",
            name="ck_generation_attempt_status",
        ),
        sa.CheckConstraint("cost_minor_units IS NULL OR cost_minor_units >= 0", name="ck_generation_attempt_cost"),
        sa.CheckConstraint(
            "submission_accounting_state IN ('NOT_CAPTURED','PENDING','CAPTURED')",
            name="ck_generation_attempt_accounting_state",
        ),
        sa.CheckConstraint(
            "(kind = 'REPAIR' AND source_verdict_id IS NOT NULL) OR "
            "(kind <> 'REPAIR' AND source_verdict_id IS NULL)",
            name="ck_generation_attempt_repair_verdict",
        ),
    )
    for column in ("job_id", "status", "provider_job_id", "result_asset_id"):
        op.create_index(f"ix_generation_attempts_{column}", "generation_attempts", [column])
    op.create_index(
        "uq_generation_attempt_initial_job",
        "generation_attempts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'INITIAL'"),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_generation_jobs_active_attempt",
        "generation_jobs",
        "generation_attempts",
        ["active_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_qa_verdicts() -> None:
    op.create_table(
        "qa_verdicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checker_version", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("response_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["attempt_id"], ["generation_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("attempt_id", "candidate_asset_id", name="uq_qa_verdict_attempt_candidate"),
        sa.CheckConstraint("decision IN ('PASS','REPAIR','REJECT')", name="ck_qa_verdict_decision"),
        sa.CheckConstraint("char_length(response_sha256) = 64", name="ck_qa_verdict_response_hash"),
        sa.CheckConstraint("jsonb_typeof(reasons) = 'array'", name="ck_qa_verdict_reasons_array"),
        sa.CheckConstraint("jsonb_typeof(metrics) = 'object'", name="ck_qa_verdict_metrics_object"),
    )
    for column in ("job_id", "attempt_id", "candidate_asset_id"):
        op.create_index(f"ix_qa_verdicts_{column}", "qa_verdicts", [column])
    op.create_foreign_key(
        "fk_generation_attempts_source_verdict",
        "generation_attempts",
        "qa_verdicts",
        ["source_verdict_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _link_existing_tables() -> None:
    for name, column in (
        ("reservation_id", sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True)),
        ("generation_job_id", sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=True)),
        ("product_policy_snapshot", sa.Column("product_policy_snapshot", postgresql.JSONB(), nullable=True)),
        ("funding_policy_snapshot", sa.Column("funding_policy_snapshot", postgresql.JSONB(), nullable=True)),
        ("settlement_status", sa.Column("settlement_status", sa.String(32), server_default="UNSETTLED", nullable=False)),
        ("delivery_status", sa.Column("delivery_status", sa.String(32), server_default="PENDING", nullable=False)),
    ):
        op.add_column("orders", column)
    op.create_foreign_key(
        "fk_orders_reservation",
        "orders",
        "credit_reservations",
        ["reservation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_orders_generation_job",
        "orders",
        "generation_jobs",
        ["generation_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_orders_reservation_id", "orders", ["reservation_id"])
    op.create_unique_constraint("uq_orders_generation_job_id", "orders", ["generation_job_id"])
    op.create_foreign_key(
        "fk_media_assets_generation_job",
        "media_assets",
        "generation_jobs",
        ["job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_asset_access_grants_generation_job",
        "asset_access_grants",
        "generation_jobs",
        ["job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_asset_access_grants_generation_attempt",
        "asset_access_grants",
        "generation_attempts",
        ["attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_credit_reservations_generation_attempt",
        "credit_reservations",
        "generation_attempts",
        ["provider_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "order_entitlements",
        sa.Column("unlock_grant_lot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "order_entitlements",
        sa.Column(
            "unlock_root_transaction_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_order_entitlements_unlock_grant_lot",
        "order_entitlements",
        "credit_grant_lots",
        ["unlock_grant_lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_order_entitlements_unlock_root_transaction",
        "order_entitlements",
        "credit_transactions",
        ["unlock_root_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_order_entitlements_unlock_grant_lot_id",
        "order_entitlements",
        ["unlock_grant_lot_id"],
    )
    op.create_index(
        "ix_order_entitlements_unlock_root_transaction_id",
        "order_entitlements",
        ["unlock_root_transaction_id"],
    )
    op.create_unique_constraint(
        "uq_order_entitlements_unlock_grant_lot",
        "order_entitlements",
        ["unlock_grant_lot_id"],
    )
    op.create_check_constraint(
        "ck_order_entitlement_unlock_pair",
        "order_entitlements",
        "(unlock_grant_lot_id IS NULL AND unlock_root_transaction_id IS NULL) OR "
        "(unlock_grant_lot_id IS NOT NULL AND unlock_root_transaction_id IS NOT NULL)",
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_entitlement_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_entitlement_transition_guard$
            BEGIN
                IF OLD.order_id <> NEW.order_id
                   OR OLD.user_id <> NEW.user_id
                   OR OLD.reservation_id <> NEW.reservation_id
                   OR OLD.unlock_grant_lot_id IS DISTINCT FROM NEW.unlock_grant_lot_id
                   OR OLD.unlock_root_transaction_id IS DISTINCT FROM NEW.unlock_root_transaction_id
                   OR OLD.access_tier <> NEW.access_tier
                   OR OLD.retention_tier <> NEW.retention_tier
                   OR NEW.expires_at < OLD.expires_at
                   OR OLD.status <> 'ACTIVE'
                   OR NEW.status <> 'REVOKED'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoke_reason IS NULL THEN
                    RAISE EXCEPTION 'invalid entitlement transition' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_entitlement_transition_guard$;
            """
        )
    )


def _create_transition_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_generation_job_transition()
            RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
            SET search_path = pg_catalog, public
            AS $guard_generation_job_transition$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'generation job is historical' USING ERRCODE = '23514';
                END IF;
                IF OLD.order_id <> NEW.order_id
                   OR OLD.submission_correlation_id <> NEW.submission_correlation_id
                   OR OLD.payload_version <> NEW.payload_version
                   OR OLD.api_deployment_id <> NEW.api_deployment_id
                   OR OLD.runtime_bundle_id <> NEW.runtime_bundle_id
                   OR OLD.expected_worker_image_digest <> NEW.expected_worker_image_digest THEN
                    RAISE EXCEPTION 'generation job identity changed' USING ERRCODE = '23514';
                END IF;
                IF NEW.active_attempt_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.generation_attempts
                     WHERE id = NEW.active_attempt_id AND job_id = NEW.id
                ) THEN
                    RAISE EXCEPTION 'active attempt belongs to another job' USING ERRCODE = '23514';
                END IF;
                IF OLD.status <> NEW.status AND NOT (
                    (OLD.status = 'QUEUED' AND NEW.status IN ('ACTIVE','CANCELLED')) OR
                    (OLD.status = 'ACTIVE' AND NEW.status IN ('RECONCILING','FINISHED','FAILED','CANCELLED')) OR
                    (OLD.status = 'RECONCILING' AND NEW.status IN ('ACTIVE','FINISHED','FAILED','CANCELLED'))
                ) THEN
                    RAISE EXCEPTION 'invalid generation job transition' USING ERRCODE = '23514';
                END IF;
                IF NEW.fencing_token < OLD.fencing_token THEN
                    RAISE EXCEPTION 'generation fence regressed' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $guard_generation_job_transition$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_generation_jobs_transition
            BEFORE UPDATE OR DELETE ON public.generation_jobs
            FOR EACH ROW EXECUTE FUNCTION public.guard_generation_job_transition();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_generation_attempt_transition()
            RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
            SET search_path = pg_catalog, public
            AS $guard_generation_attempt_transition$
            DECLARE job_correlation text;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'generation attempt is historical' USING ERRCODE = '23514';
                END IF;
                SELECT submission_correlation_id::text INTO STRICT job_correlation
                  FROM public.generation_jobs WHERE id = NEW.job_id;
                IF NEW.kind = 'INITIAL' AND NEW.client_request_id <> job_correlation THEN
                    RAISE EXCEPTION 'initial correlation mismatch' USING ERRCODE = '23514';
                END IF;
                IF NEW.kind <> 'INITIAL' AND NEW.submission_accounting_state <> 'NOT_CAPTURED' THEN
                    RAISE EXCEPTION 'non-initial attempt cannot capture' USING ERRCODE = '23514';
                END IF;
                IF NEW.kind = 'REPAIR' AND NOT EXISTS (
                    SELECT 1 FROM public.qa_verdicts
                     WHERE id = NEW.source_verdict_id
                       AND job_id = NEW.job_id
                       AND decision = 'REPAIR'
                ) THEN
                    RAISE EXCEPTION 'repair verdict lineage mismatch' USING ERRCODE = '23514';
                END IF;
                IF NEW.status IN ('SUBMITTED','FINISHED')
                   AND (NEW.provider_job_id IS NULL OR btrim(NEW.provider_job_id) = '') THEN
                    RAISE EXCEPTION 'submitted attempt lacks provider job id' USING ERRCODE = '23514';
                END IF;
                IF NEW.result_asset_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.media_assets
                     WHERE id = NEW.result_asset_id AND job_id = NEW.job_id
                ) THEN
                    RAISE EXCEPTION 'attempt result asset belongs to another job' USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF OLD.job_id <> NEW.job_id OR OLD.attempt_number <> NEW.attempt_number
                       OR OLD.kind <> NEW.kind OR OLD.provider <> NEW.provider
                       OR OLD.client_request_id <> NEW.client_request_id
                       OR OLD.source_verdict_id IS DISTINCT FROM NEW.source_verdict_id THEN
                        RAISE EXCEPTION 'generation attempt identity changed' USING ERRCODE = '23514';
                    END IF;
                    IF OLD.status <> NEW.status AND NOT (
                        (OLD.status = 'PREPARED' AND NEW.status IN ('SUBMITTING','FAILED')) OR
                        (OLD.status = 'SUBMITTING' AND NEW.status IN ('SUBMITTED','UNKNOWN','FAILED')) OR
                        (OLD.status = 'SUBMITTED' AND NEW.status IN ('UNKNOWN','FINISHED','FAILED')) OR
                        (OLD.status = 'UNKNOWN' AND NEW.status IN ('SUBMITTED','FINISHED','FAILED'))
                    ) THEN
                        RAISE EXCEPTION 'invalid generation attempt transition' USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $guard_generation_attempt_transition$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_generation_attempts_transition
            BEFORE INSERT OR UPDATE OR DELETE ON public.generation_attempts
            FOR EACH ROW EXECUTE FUNCTION public.guard_generation_attempt_transition();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.qa_verdict_append_only_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
            SET search_path = pg_catalog, public
            AS $qa_verdict_append_only_guard$
            BEGIN
                IF TG_OP <> 'INSERT' THEN
                    RAISE EXCEPTION 'qa verdict is append only' USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM public.generation_attempts AS attempt
                      JOIN public.media_assets AS candidate
                        ON candidate.id = NEW.candidate_asset_id
                     WHERE attempt.id = NEW.attempt_id
                       AND attempt.job_id = NEW.job_id
                       AND attempt.result_asset_id = NEW.candidate_asset_id
                       AND candidate.job_id = NEW.job_id
                ) THEN
                    RAISE EXCEPTION 'qa verdict lineage mismatch' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $qa_verdict_append_only_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_qa_verdicts_append_only
            BEFORE INSERT OR UPDATE OR DELETE ON public.qa_verdicts
            FOR EACH ROW EXECUTE FUNCTION public.qa_verdict_append_only_guard();
            """
        )
    )
    op.execute(
        sa.text(
            """
            REVOKE ALL ON FUNCTION public.guard_generation_job_transition() FROM PUBLIC;
            """
        )
    )
    op.execute(
        sa.text(
            """
            REVOKE ALL ON FUNCTION public.guard_generation_attempt_transition() FROM PUBLIC;
            """
        )
    )
    op.execute(
        sa.text(
            """
            REVOKE ALL ON FUNCTION public.qa_verdict_append_only_guard() FROM PUBLIC;
            """
        )
    )


def _secure_generation_tables() -> None:
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO vowpic_generation_service"))
    for table in GENERATION_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))
        privileges = "SELECT, INSERT" if table == "qa_verdicts" else "SELECT, INSERT, UPDATE"
        op.execute(
            sa.text(
                f"GRANT {privileges} ON TABLE public.{table} "
                "TO vowpic_generation_service"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_generation_service ON public.{table} "
                "FOR ALL TO vowpic_generation_service USING (true) WITH CHECK (true)"
            )
        )


def upgrade() -> None:
    _create_generation_role()
    _create_generation_jobs()
    _create_generation_attempts()
    _create_qa_verdicts()
    _link_existing_tables()
    _create_transition_guards()
    _secure_generation_tables()


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_qa_verdicts_append_only ON public.qa_verdicts"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.qa_verdict_append_only_guard()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_generation_attempts_transition ON public.generation_attempts"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_generation_attempt_transition()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_generation_jobs_transition ON public.generation_jobs"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_generation_job_transition()"))
    op.drop_constraint("fk_credit_reservations_generation_attempt", "credit_reservations", type_="foreignkey")
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.commercial_entitlement_transition_guard()
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $commercial_entitlement_transition_guard$
            BEGIN
                IF OLD.order_id <> NEW.order_id
                   OR OLD.user_id <> NEW.user_id
                   OR OLD.reservation_id <> NEW.reservation_id
                   OR OLD.access_tier <> NEW.access_tier
                   OR OLD.retention_tier <> NEW.retention_tier
                   OR NEW.expires_at < OLD.expires_at
                   OR OLD.status <> 'ACTIVE'
                   OR NEW.status <> 'REVOKED'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoke_reason IS NULL THEN
                    RAISE EXCEPTION 'invalid entitlement transition' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $commercial_entitlement_transition_guard$;
            """
        )
    )
    op.drop_constraint(
        "ck_order_entitlement_unlock_pair",
        "order_entitlements",
        type_="check",
    )
    op.drop_constraint(
        "uq_order_entitlements_unlock_grant_lot",
        "order_entitlements",
        type_="unique",
    )
    op.drop_index(
        "ix_order_entitlements_unlock_root_transaction_id",
        table_name="order_entitlements",
    )
    op.drop_index(
        "ix_order_entitlements_unlock_grant_lot_id",
        table_name="order_entitlements",
    )
    op.drop_constraint(
        "fk_order_entitlements_unlock_root_transaction",
        "order_entitlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_order_entitlements_unlock_grant_lot",
        "order_entitlements",
        type_="foreignkey",
    )
    op.drop_column("order_entitlements", "unlock_root_transaction_id")
    op.drop_column("order_entitlements", "unlock_grant_lot_id")
    op.drop_constraint("fk_asset_access_grants_generation_attempt", "asset_access_grants", type_="foreignkey")
    op.drop_constraint("fk_asset_access_grants_generation_job", "asset_access_grants", type_="foreignkey")
    op.drop_constraint("fk_media_assets_generation_job", "media_assets", type_="foreignkey")
    op.drop_constraint("uq_orders_generation_job_id", "orders", type_="unique")
    op.drop_constraint("uq_orders_reservation_id", "orders", type_="unique")
    op.drop_constraint("fk_orders_generation_job", "orders", type_="foreignkey")
    op.drop_constraint("fk_orders_reservation", "orders", type_="foreignkey")
    for column in (
        "delivery_status",
        "settlement_status",
        "funding_policy_snapshot",
        "product_policy_snapshot",
        "generation_job_id",
        "reservation_id",
    ):
        op.drop_column("orders", column)
    op.drop_constraint("fk_generation_jobs_active_attempt", "generation_jobs", type_="foreignkey")
    op.drop_column("generation_jobs", "active_attempt_id")
    op.drop_constraint(
        "fk_generation_attempts_source_verdict",
        "generation_attempts",
        type_="foreignkey",
    )
    op.drop_table("qa_verdicts")
    op.drop_table("generation_attempts")
    op.drop_table("generation_jobs")
    op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM vowpic_generation_service"))
    op.execute(sa.text("DROP ROLE IF EXISTS vowpic_generation_service"))
