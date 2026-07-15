"""Add normalized Web identity, revocable sessions, and account lineage.

Revision ID: 20260710_0014
Revises: 20260712_0014
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0014"
down_revision = "20260712_0014"
branch_labels = None
depends_on = None


IDENTITY_TABLES = (
    "user_identities",
    "oauth_login_intents",
    "auth_sessions",
    "auth_refresh_tokens",
    "account_claim_proofs",
    "identity_email_conflicts",
    "user_account_merges",
    "account_tombstones",
)

USER_OWNED_TABLES = {
    "users": "id = public.app_current_user_id()",
    "user_credits": "user_id = public.app_current_user_id()",
    "credit_transactions": "user_id = public.app_current_user_id()",
    "credit_purchases": "user_id = public.app_current_user_id()",
    "orders": "user_id = public.app_current_user_id()",
    "live_portrait_jobs": "user_id = public.app_current_user_id()",
    "user_subscriptions": "user_id = public.app_current_user_id()",
    "subscription_credit_grants": "user_id = public.app_current_user_id()",
}

RETAINED_USER_FACT_TABLES = (
    "credit_transactions",
    "credit_purchases",
    "orders",
    "subscription_credit_grants",
    "user_subscriptions",
    "user_credits",
    "live_portrait_jobs",
)

TASK6_USER_COLUMN_COMMENTS = {
    "openid": (
        sa.String(length=64),
        "Legacy identity alias; internal compatibility only",
        "Stable local identity key",
    ),
    "username": (
        sa.String(length=64),
        "Legacy username/profile alias; public password login is retired",
        "Username for password-based sign-in",
    ),
    "password": (
        sa.String(length=255),
        "Legacy password hash pending identity contract migration",
        "Bcrypt password hash for password-based sign-in",
    ),
    "auth_provider": (
        sa.String(length=32),
        "External auth provider, e.g. supabase/google",
        None,
    ),
    "auth_subject": (
        sa.String(length=128),
        "External provider subject/user id",
        None,
    ),
    "email": (
        sa.String(length=255),
        "Non-authoritative profile email; identity is provider plus subject",
        None,
    ),
    "unionid": (
        sa.String(length=64),
        "Legacy provider identity alias pending contract migration",
        "WeChat UnionID (if available)",
    ),
}


def _role_exists(role_name: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
            {"role_name": role_name},
        ).scalar_one_or_none()
        is not None
    )


def _table_exists(table_name: str) -> bool:
    return table_name in set(sa.inspect(op.get_bind()).get_table_names(schema="public"))


def _supabase_auth_available() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'auth' AND p.proname = 'uid'
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
        is not None
    )


def _create_nonlogin_role(role_name: str) -> None:
    if role_name not in {"vowpic_identity_owner", "vowpic_identity_service"}:
        raise RuntimeError(f"unsupported identity role: {role_name}")
    bind = op.get_bind()
    existing_role = bind.execute(
        sa.text(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = :role_name"
        ),
        {"role_name": role_name},
    ).mappings().one_or_none()
    if existing_role is not None:
        unsafe_attributes = [
            attribute
            for attribute, enabled in existing_role.items()
            if enabled
        ]
        if unsafe_attributes:
            raise RuntimeError(
                f"unsafe pre-existing identity role {role_name}: "
                f"{', '.join(unsafe_attributes)} must be disabled"
            )
        return
    op.execute(
        sa.text(
            f"CREATE ROLE {role_name} NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
    )


def _drop_email_identity_uniqueness() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints("users", schema="public"):
        if constraint.get("column_names") == ["email"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "users", type_="unique")

    inspector = sa.inspect(bind)
    for index in inspector.get_indexes("users", schema="public"):
        if not index.get("unique") or index.get("column_names") != ["email"]:
            continue
        if index.get("duplicates_constraint") or not index.get("name"):
            continue
        op.drop_index(index["name"], table_name="users")


def _set_task6_user_column_comments(*, restore_legacy: bool) -> None:
    for column_name, (column_type, task6_comment, legacy_comment) in (
        TASK6_USER_COLUMN_COMMENTS.items()
    ):
        op.alter_column(
            "users",
            column_name,
            existing_type=column_type,
            comment=legacy_comment if restore_legacy else task6_comment,
            existing_comment=task6_comment if restore_legacy else legacy_comment,
        )


def _replace_user_fk_with_restrict(table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    matching = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(table_name, schema="public")
        if foreign_key.get("constrained_columns") == ["user_id"]
        and foreign_key.get("referred_table") == "users"
        and foreign_key.get("referred_columns") == ["id"]
    ]
    if len(matching) != 1 or not matching[0].get("name"):
        raise RuntimeError(f"expected exactly one users FK on {table_name}.user_id")
    op.drop_constraint(matching[0]["name"], table_name, type_="foreignkey")
    op.create_foreign_key(
        f"{table_name}_user_id_fkey",
        table_name,
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_identity_tables() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("verified_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        sa.CheckConstraint(
            "provider = 'supabase'",
            name="ck_user_identities_provider_supabase",
        ),
        sa.CheckConstraint("btrim(subject) <> ''", name="ck_user_identities_subject_nonempty"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    op.create_index(
        "ix_user_identities_user_active",
        "user_identities",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "oauth_login_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_path", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_oauth_login_intents_token_hash"),
        sa.CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_oauth_login_intents_token_hash_length",
        ),
        sa.CheckConstraint(
            "char_length(browser_binding_hash) = 64",
            name="ck_oauth_login_intents_browser_hash_length",
        ),
        sa.CheckConstraint(
            "left(redirect_path, 1) = '/' AND left(redirect_path, 2) <> '//' "
            "AND position(chr(92) in redirect_path) = 0 "
            "AND redirect_path !~ '[[:cntrl:]]'",
            name="ck_oauth_login_intents_local_redirect",
        ),
    )
    op.create_index("ix_oauth_login_intents_expires_at", "oauth_login_intents", ["expires_at"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acceptance_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["acceptance_binding_id"],
            ["acceptance_identity_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("family_id", name="uq_auth_sessions_family_id"),
        sa.UniqueConstraint(
            "acceptance_binding_id",
            name="uq_auth_sessions_acceptance_binding_id",
        ),
        sa.CheckConstraint("token_version >= 1", name="ck_auth_sessions_token_version_positive"),
        sa.CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name="ck_auth_sessions_csrf_hash_length",
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replacement_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replacement_token_id"],
            ["auth_refresh_tokens.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "session_id",
            "generation",
            name="uq_auth_refresh_tokens_session_generation",
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_token_hash"),
        sa.CheckConstraint("generation >= 1", name="ck_auth_refresh_tokens_generation_positive"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'USED', 'REVOKED')",
            name="ck_auth_refresh_tokens_status",
        ),
        sa.CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_auth_refresh_tokens_hash_length",
        ),
        sa.CheckConstraint(
            "replacement_token_id IS NULL OR replacement_token_id <> id",
            name="ck_auth_refresh_tokens_replacement_not_self",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND used_at IS NULL AND revoked_at IS NULL "
            "AND replacement_token_id IS NULL) "
            "OR (status = 'USED' AND used_at IS NOT NULL AND revoked_at IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_auth_refresh_tokens_state_timestamps",
        ),
    )
    op.create_index("ix_auth_refresh_tokens_session_id", "auth_refresh_tokens", ["session_id"])
    op.create_index("ix_auth_refresh_tokens_status", "auth_refresh_tokens", ["status"])
    op.create_index("ix_auth_refresh_tokens_expires_at", "auth_refresh_tokens", ["expires_at"])

    op.create_table(
        "account_claim_proofs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legacy_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proof_type", sa.String(length=32), nullable=False),
        sa.Column("external_reference_hash", sa.String(length=64), nullable=False),
        sa.Column("verifier_actor", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_merge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("audit_request_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legacy_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "proof_type",
            "external_reference_hash",
            name="uq_account_claim_proofs_external_reference",
        ),
        sa.UniqueConstraint(
            "consumed_by_merge_id",
            name="uq_account_claim_proofs_consumed_merge",
        ),
        sa.CheckConstraint(
            "proof_type IN ('VERIFIED_PAYMENT', 'VERIFIED_SUPPORT_CASE')",
            name="ck_account_claim_proofs_type",
        ),
        sa.CheckConstraint(
            "char_length(external_reference_hash) = 64",
            name="ck_account_claim_proofs_reference_hash_length",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL AND consumed_by_merge_id IS NULL) "
            "OR (consumed_at IS NOT NULL AND consumed_by_merge_id IS NOT NULL)",
            name="ck_account_claim_proofs_consumption_pair",
        ),
    )
    op.create_index("ix_account_claim_proofs_canonical_user_id", "account_claim_proofs", ["canonical_user_id"])
    op.create_index("ix_account_claim_proofs_legacy_user_id", "account_claim_proofs", ["legacy_user_id"])
    op.create_index("ix_account_claim_proofs_expires_at", "account_claim_proofs", ["expires_at"])
    op.create_index("ix_account_claim_proofs_audit_request_id", "account_claim_proofs", ["audit_request_id"])

    op.create_table(
        "identity_email_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legacy_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_hmac", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="OPEN", nullable=False),
        sa.Column("discovery_source", sa.String(length=64), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_audit_id", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["canonical_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legacy_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "canonical_user_id",
            "legacy_user_id",
            "email_hmac",
            name="uq_identity_email_conflicts_pair_hash",
        ),
        sa.CheckConstraint(
            "canonical_user_id <> legacy_user_id",
            name="ck_identity_email_conflicts_distinct_users",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED_MERGED', 'RESOLVED_DISTINCT')",
            name="ck_identity_email_conflicts_status",
        ),
        sa.CheckConstraint(
            "char_length(email_hmac) = 64",
            name="ck_identity_email_conflicts_hmac_length",
        ),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL AND resolution_audit_id IS NULL) "
            "OR (status <> 'OPEN' AND resolved_at IS NOT NULL "
            "AND resolution_audit_id IS NOT NULL)",
            name="ck_identity_email_conflicts_resolution",
        ),
    )
    op.create_index("ix_identity_email_conflicts_canonical_user_id", "identity_email_conflicts", ["canonical_user_id"])
    op.create_index("ix_identity_email_conflicts_legacy_user_id", "identity_email_conflicts", ["legacy_user_id"])
    op.create_index("ix_identity_email_conflicts_status", "identity_email_conflicts", ["status"])

    op.create_table(
        "user_account_merges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legacy_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_proof_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_request_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legacy_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["claim_proof_id"], ["account_claim_proofs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("legacy_user_id", name="uq_user_account_merges_legacy_user"),
        sa.UniqueConstraint("claim_proof_id", name="uq_user_account_merges_claim_proof"),
        sa.CheckConstraint(
            "canonical_user_id <> legacy_user_id",
            name="ck_user_account_merges_distinct_users",
        ),
    )
    op.create_index("ix_user_account_merges_canonical_user_id", "user_account_merges", ["canonical_user_id"])
    op.create_index("ix_user_account_merges_legacy_user_id", "user_account_merges", ["legacy_user_id"])
    op.create_index("ix_user_account_merges_audit_request_id", "user_account_merges", ["audit_request_id"])
    op.create_foreign_key(
        "fk_account_claim_proofs_consumed_merge",
        "account_claim_proofs",
        "user_account_merges",
        ["consumed_by_merge_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "account_tombstones",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("closure_reason", sa.String(length=64), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_cleanup_pending", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("audit_request_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "btrim(closure_reason) <> ''",
            name="ck_account_tombstones_reason_nonempty",
        ),
    )
    op.create_index("ix_account_tombstones_audit_request_id", "account_tombstones", ["audit_request_id"])


def _create_identity_roles_and_rls() -> None:
    _create_nonlogin_role("vowpic_identity_owner")
    _create_nonlogin_role("vowpic_identity_service")
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO vowpic_identity_owner"))
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO vowpic_identity_service"))
    op.execute(
        sa.text(
            "CREATE SEQUENCE public.identity_legacy_fallback_uses_seq "
            "AS bigint START WITH 1 INCREMENT BY 1 NO CYCLE"
        )
    )
    op.execute(
        sa.text(
            "ALTER SEQUENCE public.identity_legacy_fallback_uses_seq "
            "OWNER TO vowpic_identity_owner"
        )
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON SEQUENCE public.identity_legacy_fallback_uses_seq FROM PUBLIC"
        )
    )
    for role_name in ("authenticated", "vowpic_runtime", "vowpic_control_writer"):
        if _role_exists(role_name):
            op.execute(
                sa.text(
                    "REVOKE ALL ON SEQUENCE public.identity_legacy_fallback_uses_seq "
                    f"FROM {role_name}"
                )
            )
    op.execute(
        sa.text(
            "GRANT SELECT ON SEQUENCE public.identity_legacy_fallback_uses_seq "
            "TO vowpic_identity_service"
        )
    )

    immutable_tables = {"user_account_merges"}
    for table_name in IDENTITY_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC"))
        for role_name in ("authenticated", "vowpic_runtime", "vowpic_control_writer"):
            if _role_exists(role_name):
                op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table_name} FROM {role_name}"))
        privileges = "SELECT, INSERT" if table_name in immutable_tables else "SELECT, INSERT, UPDATE"
        op.execute(
            sa.text(
                f"GRANT {privileges} ON TABLE public.{table_name} "
                "TO vowpic_identity_service"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table_name}_identity_service_all ON public.{table_name} "
                "FOR ALL TO vowpic_identity_service USING (true) WITH CHECK (true)"
            )
        )

    op.execute(sa.text("GRANT SELECT ON TABLE public.users TO vowpic_identity_owner"))
    op.execute(sa.text("GRANT SELECT ON TABLE public.user_identities TO vowpic_identity_owner"))
    op.execute(
        sa.text(
            "CREATE POLICY user_identities_identity_owner_select ON public.user_identities "
            "FOR SELECT TO vowpic_identity_owner USING (true)"
        )
    )


def _create_current_user_resolver() -> None:
    op.execute(sa.text("ALTER TABLE public.users ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE public.users FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS users_identity_owner_lookup ON public.users"))
    op.execute(
        sa.text(
            "CREATE POLICY users_identity_owner_lookup ON public.users "
            "FOR SELECT TO vowpic_identity_owner USING (true)"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.app_current_user_id()
            RETURNS uuid
            LANGUAGE plpgsql
            STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $resolver$
            DECLARE
                raw_claims text;
                claims jsonb;
                subject_claim text;
                external_provider text;
                resolved_user_id uuid;
            BEGIN
                raw_claims := current_setting('request.jwt.claims', true);
                IF raw_claims IS NULL OR btrim(raw_claims) = '' THEN
                    RETURN NULL;
                END IF;
                BEGIN
                    claims := raw_claims::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN NULL;
                END;
                IF jsonb_typeof(claims) IS DISTINCT FROM 'object'
                   OR jsonb_typeof(claims -> 'app_metadata') IS DISTINCT FROM 'object'
                   OR jsonb_typeof(claims -> 'sub') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(claims -> 'app_metadata' -> 'provider')
                        IS DISTINCT FROM 'string'
                   OR (
                       claims ? 'is_anonymous'
                       AND (
                           jsonb_typeof(claims -> 'is_anonymous')
                               IS DISTINCT FROM 'boolean'
                           OR claims -> 'is_anonymous' IS DISTINCT FROM 'false'::jsonb
                       )
                   ) THEN
                    RETURN NULL;
                END IF;
                subject_claim := claims ->> 'sub';
                external_provider := claims #>> '{app_metadata,provider}';
                IF subject_claim IS NULL
                   OR btrim(subject_claim) = ''
                   OR subject_claim <> btrim(subject_claim)
                   OR char_length(subject_claim) > 255
                   OR external_provider <> 'google' THEN
                    RETURN NULL;
                END IF;

                SELECT identity.user_id
                INTO resolved_user_id
                FROM public.user_identities AS identity
                WHERE identity.provider = 'supabase'
                  AND identity.subject = subject_claim
                  AND identity.revoked_at IS NULL
                LIMIT 1;
                IF resolved_user_id IS NOT NULL THEN
                    RETURN resolved_user_id;
                END IF;

                SELECT legacy.id
                INTO resolved_user_id
                FROM public.users AS legacy
                WHERE legacy.auth_provider = 'supabase'
                  AND legacy.auth_subject = subject_claim
                LIMIT 1;
                IF resolved_user_id IS NOT NULL THEN
                    PERFORM pg_catalog.nextval(
                        'public.identity_legacy_fallback_uses_seq'::regclass
                    );
                END IF;
                RETURN resolved_user_id;
            END;
            $resolver$;
            """
        )
    )
    op.execute(sa.text("ALTER FUNCTION public.app_current_user_id() OWNER TO vowpic_identity_owner"))
    op.execute(sa.text("REVOKE ALL ON FUNCTION public.app_current_user_id() FROM PUBLIC"))
    if _role_exists("authenticated"):
        op.execute(sa.text("GRANT EXECUTE ON FUNCTION public.app_current_user_id() TO authenticated"))

    for table_name, predicate in USER_OWNED_TABLES.items():
        if not _table_exists(table_name):
            continue
        op.execute(sa.text(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_select_own ON public.{table_name}"))
        if _role_exists("authenticated"):
            op.execute(sa.text(f"GRANT SELECT ON TABLE public.{table_name} TO authenticated"))
            op.execute(
                sa.text(
                    f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                    f"ON TABLE public.{table_name} FROM authenticated"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE POLICY {table_name}_select_own ON public.{table_name} "
                    f"FOR SELECT TO authenticated USING ({predicate})"
                )
            )


def _create_identity_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_user_identity_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $identity_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.user_id IS DISTINCT FROM OLD.user_id
                   OR NEW.provider IS DISTINCT FROM OLD.provider
                   OR NEW.subject IS DISTINCT FROM OLD.subject
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
                    RAISE EXCEPTION 'identity binding or revocation cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $identity_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_user_identities_state_guard "
            "BEFORE UPDATE ON public.user_identities "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_user_identity_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_oauth_login_intent_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $oauth_intent_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
                   OR NEW.browser_binding_hash IS DISTINCT FROM OLD.browser_binding_hash
                   OR NEW.redirect_path IS DISTINCT FROM OLD.redirect_path
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (OLD.consumed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
                    RAISE EXCEPTION 'OAuth login intent consumption cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $oauth_intent_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_oauth_login_intents_state_guard "
            "BEFORE UPDATE ON public.oauth_login_intents "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_oauth_login_intent_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_auth_session_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $auth_session_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.user_id IS DISTINCT FROM OLD.user_id
                   OR NEW.acceptance_binding_id IS DISTINCT FROM OLD.acceptance_binding_id
                   OR NEW.family_id IS DISTINCT FROM OLD.family_id
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.token_version < OLD.token_version
                   OR (OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
                    RAISE EXCEPTION 'auth session revocation or version cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $auth_session_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_auth_sessions_state_guard "
            "BEFORE UPDATE ON public.auth_sessions "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_auth_session_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_account_claim_proof_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $claim_proof_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.canonical_user_id IS DISTINCT FROM OLD.canonical_user_id
                   OR NEW.legacy_user_id IS DISTINCT FROM OLD.legacy_user_id
                   OR NEW.proof_type IS DISTINCT FROM OLD.proof_type
                   OR NEW.external_reference_hash IS DISTINCT FROM OLD.external_reference_hash
                   OR NEW.verifier_actor IS DISTINCT FROM OLD.verifier_actor
                   OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.audit_request_id IS DISTINCT FROM OLD.audit_request_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (OLD.consumed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
                    RAISE EXCEPTION 'claim proof facts or consumption cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $claim_proof_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_account_claim_proofs_state_guard "
            "BEFORE UPDATE ON public.account_claim_proofs "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_account_claim_proof_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_identity_email_conflict_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $email_conflict_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.canonical_user_id IS DISTINCT FROM OLD.canonical_user_id
                   OR NEW.legacy_user_id IS DISTINCT FROM OLD.legacy_user_id
                   OR NEW.email_hmac IS DISTINCT FROM OLD.email_hmac
                   OR NEW.discovery_source IS DISTINCT FROM OLD.discovery_source
                   OR NEW.discovered_at IS DISTINCT FROM OLD.discovered_at
                   OR (OLD.status <> 'OPEN' AND NEW IS DISTINCT FROM OLD) THEN
                    RAISE EXCEPTION 'identity email conflict resolution cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $email_conflict_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_identity_email_conflicts_state_guard "
            "BEFORE UPDATE ON public.identity_email_conflicts "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_identity_email_conflict_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_account_tombstone_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $account_tombstone_guard$
            BEGIN
                IF NEW.user_id IS DISTINCT FROM OLD.user_id
                   OR NEW.closure_reason IS DISTINCT FROM OLD.closure_reason
                   OR NEW.closed_at IS DISTINCT FROM OLD.closed_at
                   OR NEW.audit_request_id IS DISTINCT FROM OLD.audit_request_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (
                       OLD.media_cleanup_pending = false
                       AND NEW.media_cleanup_pending IS DISTINCT FROM false
                   ) THEN
                    RAISE EXCEPTION 'account tombstone facts or cleanup cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $account_tombstone_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_account_tombstones_state_guard "
            "BEFORE UPDATE ON public.account_tombstones "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_account_tombstone_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_user_account_merge()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $merge_guard$
            DECLARE
                first_lock text;
                second_lock text;
                proof_canonical uuid;
                proof_legacy uuid;
                proof_expires_at timestamptz;
                proof_consumed_at timestamptz;
            BEGIN
                IF NEW.canonical_user_id = NEW.legacy_user_id THEN
                    RAISE EXCEPTION 'merge users must be distinct' USING ERRCODE = '23514';
                END IF;
                first_lock := least(NEW.canonical_user_id::text, NEW.legacy_user_id::text);
                second_lock := greatest(NEW.canonical_user_id::text, NEW.legacy_user_id::text);
                PERFORM pg_advisory_xact_lock(hashtextextended(first_lock, 0));
                PERFORM pg_advisory_xact_lock(hashtextextended(second_lock, 0));

                IF EXISTS (
                    SELECT 1 FROM public.user_account_merges
                    WHERE legacy_user_id = NEW.canonical_user_id
                       OR canonical_user_id = NEW.legacy_user_id
                ) THEN
                    RAISE EXCEPTION 'merge chain or cycle is forbidden' USING ERRCODE = '23514';
                END IF;

                SELECT canonical_user_id, legacy_user_id, expires_at, consumed_at
                INTO proof_canonical, proof_legacy, proof_expires_at, proof_consumed_at
                FROM public.account_claim_proofs
                WHERE id = NEW.claim_proof_id
                FOR UPDATE;
                IF NOT FOUND
                   OR proof_canonical <> NEW.canonical_user_id
                   OR proof_legacy <> NEW.legacy_user_id THEN
                    RAISE EXCEPTION 'claim proof is not bound to this exact merge' USING ERRCODE = '23514';
                END IF;
                IF proof_consumed_at IS NOT NULL OR proof_expires_at <= statement_timestamp() THEN
                    RAISE EXCEPTION 'claim proof is expired or consumed' USING ERRCODE = '23514';
                END IF;

                UPDATE public.account_claim_proofs
                SET consumed_at = statement_timestamp(), consumed_by_merge_id = NEW.id
                WHERE id = NEW.claim_proof_id AND consumed_at IS NULL;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'claim proof was consumed concurrently' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $merge_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_user_account_merges_guard "
            "BEFORE INSERT ON public.user_account_merges "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_user_account_merge()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.reject_user_account_merge_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $immutable_merge$
            BEGIN
                RAISE EXCEPTION 'user account merges are immutable' USING ERRCODE = '23514';
            END;
            $immutable_merge$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_user_account_merges_immutable "
            "BEFORE UPDATE OR DELETE ON public.user_account_merges "
            "FOR EACH ROW EXECUTE FUNCTION public.reject_user_account_merge_mutation()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_auth_refresh_token_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $refresh_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.session_id IS DISTINCT FROM OLD.session_id
                   OR NEW.generation IS DISTINCT FROM OLD.generation
                   OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR (OLD.status = 'USED' AND NEW.status NOT IN ('USED', 'REVOKED'))
                   OR (OLD.status = 'REVOKED' AND NEW IS DISTINCT FROM OLD)
                   OR (
                       OLD.used_at IS NOT NULL
                       AND NEW.used_at IS DISTINCT FROM OLD.used_at
                   )
                   OR (
                       OLD.revoked_at IS NOT NULL
                       AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
                   )
                   OR (
                       OLD.replacement_token_id IS NOT NULL
                       AND NEW.replacement_token_id IS DISTINCT FROM OLD.replacement_token_id
                   ) THEN
                    RAISE EXCEPTION 'refresh token facts or status cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $refresh_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_auth_refresh_tokens_immutable_facts "
            "BEFORE UPDATE ON public.auth_refresh_tokens "
            "FOR EACH ROW EXECUTE FUNCTION public.guard_auth_refresh_token_update()"
        )
    )


def _extend_preview_cleanup_control_plane() -> None:
    """Add one-way binding revocation and the bounded Preview cleanup lifecycle."""
    op.add_column(
        "acceptance_identity_bindings",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_acceptance_binding_revocation_unconsumed",
        "acceptance_identity_bindings",
        "revoked_at IS NULL OR (consumed_at IS NULL AND consumed_user_id IS NULL)",
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
                  NEW.consumed_user_id IS DISTINCT FROM OLD.consumed_user_id OR
                  NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
                RAISE EXCEPTION 'consumed acceptance identity is immutable';
              END IF;
              IF OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'revoked acceptance identity is immutable';
              END IF;
              IF NEW.revoked_at IS NOT NULL AND
                 (NEW.consumed_at IS NOT NULL OR NEW.consumed_user_id IS NOT NULL) THEN
                RAISE EXCEPTION 'a consumed acceptance identity cannot be revoked';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_release_activation_regression()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.phase IN ('COMPLETED', 'CLEANED', 'PASSED', 'PRODUCTION_ACCEPTED') THEN
                IF OLD.environment = 'preview'
                   AND OLD.kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')
                   AND OLD.phase = 'COMPLETED' THEN
                  IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'release activation requires version CAS';
                  END IF;
                  IF NEW.phase = 'COMPLETED' THEN
                    IF OLD.current_snapshot_hash IS NOT NULL
                       OR OLD.target_snapshot_hash IS NOT NULL
                       OR NEW.current_snapshot_hash IS NULL
                       OR NEW.target_snapshot_hash IS NULL
                       OR NEW.phase_rank <> OLD.phase_rank
                       OR (
                         to_jsonb(NEW) - 'version' - 'updated_at'
                           - 'current_snapshot_hash' - 'target_snapshot_hash'
                         IS DISTINCT FROM
                         to_jsonb(OLD) - 'version' - 'updated_at'
                           - 'current_snapshot_hash' - 'target_snapshot_hash'
                       ) THEN
                      RAISE EXCEPTION 'preview COMPLETED activation may only record its one-time snapshot';
                    END IF;
                  ELSIF NEW.phase = 'CLEANED' THEN
                    IF NEW.phase_rank <= OLD.phase_rank
                       OR (
                         to_jsonb(NEW) - 'version' - 'updated_at' - 'phase' - 'phase_rank'
                         IS DISTINCT FROM
                         to_jsonb(OLD) - 'version' - 'updated_at' - 'phase' - 'phase_rank'
                       ) THEN
                      RAISE EXCEPTION 'preview cleanup may only advance COMPLETED to CLEANED';
                    END IF;
                  ELSE
                    RAISE EXCEPTION 'terminal release activation is immutable';
                  END IF;
                  NEW.updated_at := CURRENT_TIMESTAMP;
                  RETURN NEW;
                END IF;
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


def _configure_preview_activation_indexes() -> None:
    """Serialize each protected Preview role without weakening runtime uniqueness."""
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_release_activation_preview_identity_active
            ON public.release_activations (environment, kind)
            WHERE environment = 'preview'
              AND kind = 'PREVIEW_IDENTITY'
              AND phase <> 'CLEANED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_release_activation_preview_commercial_active
            ON public.release_activations (environment, kind)
            WHERE environment = 'preview'
              AND kind = 'PREVIEW_COMMERCIAL'
              AND phase <> 'CLEANED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_release_activation_preview_commercial_attempt
            ON public.release_activations (
              environment, kind, workflow_run_id, workflow_attempt
            )
            WHERE environment = 'preview' AND kind = 'PREVIEW_COMMERCIAL'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_release_activation_preview_identity_attempt
            ON public.release_activations (
              environment, kind, workflow_run_id, workflow_attempt
            )
            WHERE environment = 'preview' AND kind = 'PREVIEW_IDENTITY'
            """
        )
    )


def _restore_pre_identity_activation_indexes() -> None:
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS public.uq_release_activation_preview_commercial_attempt"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS public.uq_release_activation_preview_commercial_active"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS public.uq_release_activation_preview_identity_attempt"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS public.uq_release_activation_preview_identity_active"
        )
    )


def upgrade() -> None:
    # Validate cluster-global roles before changing transactional application schema.
    _create_nonlogin_role("vowpic_identity_owner")
    _create_nonlogin_role("vowpic_identity_service")
    _drop_email_identity_uniqueness()
    _set_task6_user_column_comments(restore_legacy=False)
    op.alter_column(
        "users",
        "openid",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    for table_name in RETAINED_USER_FACT_TABLES:
        _replace_user_fk_with_restrict(table_name)
    _create_identity_tables()
    _create_identity_roles_and_rls()
    _create_current_user_resolver()
    _create_identity_guards()
    _extend_preview_cleanup_control_plane()
    _configure_preview_activation_indexes()


def _assert_downgrade_is_safe() -> None:
    bind = op.get_bind()
    null_openids = bind.execute(
        sa.text("SELECT count(*) FROM public.users WHERE openid IS NULL")
    ).scalar_one()
    duplicate_emails = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT email FROM public.users WHERE email IS NOT NULL "
            "GROUP BY email HAVING count(*) > 1) AS duplicates"
        )
    ).scalar_one()
    nonempty_identity_tables = [
        table_name
        for table_name in IDENTITY_TABLES
        if bind.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{table_name} LIMIT 1)")
        ).scalar_one()
    ]
    fallback_used = bind.execute(
        sa.text("SELECT is_called FROM public.identity_legacy_fallback_uses_seq")
    ).scalar_one()
    revoked_acceptance_bindings = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.acceptance_identity_bindings "
            "WHERE revoked_at IS NOT NULL"
        )
    ).scalar_one()
    unsafe_reasons = []
    if null_openids or duplicate_emails:
        unsafe_reasons.append("NULL openid or duplicate profile emails")
    if nonempty_identity_tables:
        unsafe_reasons.append(
            "retained identity/session facts in "
            + ", ".join(nonempty_identity_tables)
        )
    if fallback_used:
        unsafe_reasons.append("observed legacy identity fallback usage")
    if revoked_acceptance_bindings:
        unsafe_reasons.append("revoked acceptance identity bindings")
    if unsafe_reasons:
        raise RuntimeError(
            "identity migration downgrade is unsafe with " + "; ".join(unsafe_reasons)
        )


def _restore_legacy_supabase_rls() -> None:
    if not _supabase_auth_available() or not _role_exists("authenticated"):
        return
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.app_current_user_id()
            RETURNS uuid
            LANGUAGE sql
            STABLE
            SECURITY INVOKER
            SET search_path = public, auth
            AS $legacy_resolver$
                SELECT users.id
                FROM public.users
                WHERE (
                    users.auth_provider = 'supabase'
                    AND users.auth_subject = (select auth.uid())::text
                )
                OR users.id = (select auth.uid())
                LIMIT 1
            $legacy_resolver$;
            """
        )
    )
    for table_name, predicate in USER_OWNED_TABLES.items():
        if not _table_exists(table_name):
            continue
        op.execute(sa.text(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_select_own ON public.{table_name}"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table_name}_select_own ON public.{table_name} "
                f"FOR SELECT TO authenticated USING ({predicate})"
            )
        )


def _restore_pre_identity_control_plane_guards() -> None:
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
            """
            CREATE OR REPLACE FUNCTION prevent_release_activation_regression()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.phase IN ('COMPLETED', 'CLEANED', 'PASSED', 'PRODUCTION_ACCEPTED') THEN
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


def downgrade() -> None:
    _assert_downgrade_is_safe()
    _restore_pre_identity_activation_indexes()
    _restore_pre_identity_control_plane_guards()
    op.drop_constraint(
        "ck_acceptance_binding_revocation_unconsumed",
        "acceptance_identity_bindings",
        type_="check",
    )
    op.drop_column("acceptance_identity_bindings", "revoked_at")
    for table_name, trigger_name, function_name in (
        ("user_identities", "trg_user_identities_state_guard", "guard_user_identity_update"),
        (
            "oauth_login_intents",
            "trg_oauth_login_intents_state_guard",
            "guard_oauth_login_intent_update",
        ),
        ("auth_sessions", "trg_auth_sessions_state_guard", "guard_auth_session_update"),
        (
            "account_claim_proofs",
            "trg_account_claim_proofs_state_guard",
            "guard_account_claim_proof_update",
        ),
        (
            "identity_email_conflicts",
            "trg_identity_email_conflicts_state_guard",
            "guard_identity_email_conflict_update",
        ),
        (
            "account_tombstones",
            "trg_account_tombstones_state_guard",
            "guard_account_tombstone_update",
        ),
    ):
        op.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
        )
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS public.{function_name}()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_auth_refresh_tokens_immutable_facts ON public.auth_refresh_tokens"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_auth_refresh_token_update()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_user_account_merges_immutable ON public.user_account_merges"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.reject_user_account_merge_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_user_account_merges_guard ON public.user_account_merges"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_user_account_merge()"))

    for table_name in USER_OWNED_TABLES:
        if not _table_exists(table_name):
            continue
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_select_own ON public.{table_name}"))
        op.execute(sa.text(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY"))
        if not _supabase_auth_available():
            op.execute(sa.text(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS users_identity_owner_lookup ON public.users"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.app_current_user_id()"))
    op.execute(
        sa.text("DROP SEQUENCE IF EXISTS public.identity_legacy_fallback_uses_seq")
    )

    op.drop_constraint(
        "fk_account_claim_proofs_consumed_merge",
        "account_claim_proofs",
        type_="foreignkey",
    )
    for table_name in reversed(IDENTITY_TABLES):
        op.drop_table(table_name)

    for table_name in RETAINED_USER_FACT_TABLES:
        inspector = sa.inspect(op.get_bind())
        matching = [
            item
            for item in inspector.get_foreign_keys(table_name, schema="public")
            if item.get("constrained_columns") == ["user_id"]
            and item.get("referred_table") == "users"
        ]
        if len(matching) != 1 or not matching[0].get("name"):
            raise RuntimeError(f"expected exactly one users FK on {table_name}.user_id")
        op.drop_constraint(matching[0]["name"], table_name, type_="foreignkey")
        op.create_foreign_key(
            f"{table_name}_user_id_fkey",
            table_name,
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.alter_column(
        "users",
        "openid",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    _set_task6_user_column_comments(restore_legacy=True)
    op.create_index("ix_users_email_unique", "users", ["email"], unique=True)
    _restore_legacy_supabase_rls()
