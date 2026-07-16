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


def upgrade() -> None:
    _create_tables()
    _create_guards()
    _create_rls()


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_invite_events_append_only ON public.partner_invite_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.reject_partner_audit_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_consent_cases_guard ON public.partner_consent_cases"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_partner_consent_case_transition()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_partner_invites_guard ON public.partner_invites"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_partner_invite_transition()"))
    op.drop_constraint("fk_partner_invites_consent_event", "partner_invites", type_="foreignkey")
    for table in reversed(PARTNER_TABLES):
        op.drop_table(table)
    if _role_exists("vowpic_partner_service"):
        op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM vowpic_partner_service"))
