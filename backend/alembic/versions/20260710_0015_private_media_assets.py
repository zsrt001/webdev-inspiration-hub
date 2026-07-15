"""Add private media, upload intent, grants, quota, and deletion authority.

Revision ID: 20260710_0015
Revises: 20260710_0014
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260710_0015"
down_revision = "20260710_0014"
branch_labels = None
depends_on = None


MEDIA_TABLES = (
    "upload_batches",
    "media_assets",
    "asset_access_grants",
    "upload_quota_windows",
    "upload_quota_states",
    "upload_quota_reservations",
)


def _role_exists(role_name: str) -> bool:
    return (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role_name"),
            {"role_name": role_name},
        )
        .scalar_one_or_none()
        is not None
    )


def _create_nonlogin_media_role() -> None:
    role_name = "vowpic_media_service"
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = :role_name"
        ),
        {"role_name": role_name},
    ).mappings().one_or_none()
    if existing is not None:
        unsafe = [attribute for attribute, enabled in existing.items() if enabled]
        if unsafe:
            raise RuntimeError(
                f"unsafe pre-existing media role {role_name}: "
                f"{', '.join(unsafe)} must be disabled"
            )
        return
    op.execute(
        sa.text(
            "CREATE ROLE vowpic_media_service NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
    )


def _create_tables() -> None:
    op.create_table(
        "upload_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING_UPLOAD'"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("expected_files", sa.Integer(), nullable=False),
        sa.Column("received_files", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('PENDING_UPLOAD', 'ACTIVE', 'UPLOAD_FAILED')",
            name="ck_upload_batches_status",
        ),
        sa.CheckConstraint(
            "expected_files BETWEEN 1 AND 5",
            name="ck_upload_batches_expected_files",
        ),
        sa.CheckConstraint(
            "received_files >= 0 AND received_files <= expected_files",
            name="ck_upload_batches_received_files",
        ),
    )
    op.create_index("ix_upload_batches_owner_user_id", "upload_batches", ["owner_user_id"])
    op.create_index("ix_upload_batches_status", "upload_batches", ["status"])
    op.create_index("ix_upload_batches_request_id", "upload_batches", ["request_id"])
    op.create_index("ix_upload_batches_expires_at", "upload_batches", ["expires_at"])
    op.create_index("ix_upload_batches_lease_expires_at", "upload_batches", ["lease_expires_at"])

    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("upload_part_ordinal", sa.Integer(), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("access_level", sa.String(length=32), server_default=sa.text("'private'"), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING_UPLOAD'"),
            nullable=False,
        ),
        sa.Column("read_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.String(length=64), nullable=True),
        sa.Column("deletion_blockers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("delete_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_delete_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delete_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["upload_batch_id"], ["upload_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "storage_provider",
            "object_key",
            name="uq_media_assets_provider_object_key",
        ),
        sa.UniqueConstraint(
            "upload_batch_id",
            "upload_part_ordinal",
            name="uq_media_assets_upload_batch_part",
        ),
        sa.CheckConstraint(
            "role IN ('source', 'intermediate', 'candidate', 'qa_input', "
            "'preview_watermarked', 'final_master', 'delivery_variant', 'legacy_video')",
            name="ck_media_assets_role",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_UPLOAD', 'UPLOAD_FAILED', 'ACTIVE', "
            "'PENDING_DELETE', 'DELETE_FAILED', 'DELETED', 'QUARANTINED')",
            name="ck_media_assets_status",
        ),
        sa.CheckConstraint(
            "upload_part_ordinal IS NULL OR upload_part_ordinal >= 0",
            name="ck_media_assets_part_ordinal_nonnegative",
        ),
        sa.CheckConstraint("char_length(sha256) = 64", name="ck_media_assets_sha256_length"),
        sa.CheckConstraint("byte_size >= 0", name="ck_media_assets_byte_size_nonnegative"),
        sa.CheckConstraint("width IS NULL OR width > 0", name="ck_media_assets_width_positive"),
        sa.CheckConstraint("height IS NULL OR height > 0", name="ck_media_assets_height_positive"),
        sa.CheckConstraint(
            "delete_attempts >= 0 AND fencing_token >= 0",
            name="ck_media_assets_delete_counters_nonnegative",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_claim_id IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_claim_id IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_media_assets_delete_lease_coherent",
        ),
        sa.CheckConstraint(
            "status <> 'DELETED' OR deleted_at IS NOT NULL",
            name="ck_media_assets_deleted_timestamp",
        ),
        sa.CheckConstraint(
            "deletion_blockers IS NULL OR jsonb_typeof(deletion_blockers) = 'array'",
            name="ck_media_assets_deletion_blockers_array",
        ),
        sa.CheckConstraint(
            "status NOT IN ('PENDING_DELETE', 'DELETE_FAILED', 'DELETED') "
            "OR btrim(deletion_reason) <> ''",
            name="ck_media_assets_deletion_reason_required",
        ),
    )
    for column_name in (
        "owner_user_id",
        "upload_batch_id",
        "order_id",
        "job_id",
        "parent_asset_id",
        "role",
        "expires_at",
        "status",
        "read_revoked_at",
        "next_delete_at",
        "deleted_at",
        "lease_expires_at",
    ):
        op.create_index(f"ix_media_assets_{column_name}", "media_assets", [column_name])

    op.create_table(
        "asset_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("runtime_bundle_id", sa.String(length=128), nullable=False),
        sa.Column("target_api_deployment_id", sa.String(length=128), nullable=False),
        sa.Column("serving_deployment_role", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_reads", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("used_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_asset_access_grants_token_hash"),
        sa.CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_asset_access_grants_token_hash_length",
        ),
        sa.CheckConstraint(
            "max_reads > 0 AND used_count >= 0 AND used_count <= max_reads",
            name="ck_asset_access_grants_read_count",
        ),
    )
    for column_name in ("asset_id", "provider", "job_id", "attempt_id", "expires_at", "revoked_at"):
        op.create_index(
            f"ix_asset_access_grants_{column_name}",
            "asset_access_grants",
            [column_name],
        )

    op.create_table(
        "upload_quota_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_kind", sa.String(length=32), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempted_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "user_id",
            "window_kind",
            "window_start",
            name="uq_upload_quota_windows_user_kind_start",
        ),
        sa.CheckConstraint(
            "window_kind IN ('HOURLY_REQUESTS', 'DAILY_BYTES')",
            name="ck_upload_quota_windows_kind",
        ),
        sa.CheckConstraint(
            "request_count >= 0 AND attempted_bytes >= 0 AND reserved_bytes >= 0",
            name="ck_upload_quota_windows_counters_nonnegative",
        ),
    )
    op.create_index("ix_upload_quota_windows_user_id", "upload_quota_windows", ["user_id"])
    op.create_index("ix_upload_quota_windows_window_start", "upload_quota_windows", ["window_start"])

    op.create_table(
        "upload_quota_states",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("active_slots", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "active_slots BETWEEN 0 AND 2",
            name="ck_upload_quota_states_active_slots",
        ),
        sa.CheckConstraint("version >= 0", name="ck_upload_quota_states_version"),
    )

    op.create_table(
        "upload_quota_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_window_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_ordinal", sa.Integer(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("actual_attempted_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'RESERVED'"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slot_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["upload_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["quota_window_id"],
            ["upload_quota_windows.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "part_ordinal",
            name="uq_upload_quota_reservations_batch_part",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'SETTLED', 'RELEASED')",
            name="ck_upload_quota_reservations_status",
        ),
        sa.CheckConstraint(
            "part_ordinal >= 0 AND reserved_bytes >= 0 "
            "AND actual_attempted_bytes >= 0",
            name="ck_upload_quota_reservations_counters",
        ),
    )
    op.create_index(
        "ix_upload_quota_reservations_batch_id",
        "upload_quota_reservations",
        ["batch_id"],
    )
    op.create_index(
        "ix_upload_quota_reservations_quota_window_id",
        "upload_quota_reservations",
        ["quota_window_id"],
    )
    op.create_index(
        "ix_upload_quota_reservations_status",
        "upload_quota_reservations",
        ["status"],
    )


def _add_asset_reference_columns() -> None:
    for column_name, comment in (
        ("source_asset_ids", "Canonical private source asset UUIDs"),
        ("preview_asset_ids", "Canonical private preview asset UUIDs"),
        ("final_asset_ids", "Canonical private final/delivery asset UUIDs"),
    ):
        op.add_column(
            "orders",
            sa.Column(column_name, postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment=comment),
        )
        op.create_check_constraint(
            f"ck_orders_{column_name}_array",
            "orders",
            f"{column_name} IS NULL OR jsonb_typeof({column_name}) = 'array'",
        )

    op.add_column(
        "live_portrait_jobs",
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "live_portrait_jobs",
        sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_live_portrait_jobs_source_asset",
        "live_portrait_jobs",
        "media_assets",
        ["source_asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_live_portrait_jobs_video_asset",
        "live_portrait_jobs",
        "media_assets",
        ["video_asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_live_portrait_jobs_source_asset_id",
        "live_portrait_jobs",
        ["source_asset_id"],
    )
    op.create_index(
        "ix_live_portrait_jobs_video_asset_id",
        "live_portrait_jobs",
        ["video_asset_id"],
    )


def _create_media_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.guard_media_asset_update()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $media_asset_update_guard$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.upload_batch_id IS DISTINCT FROM OLD.upload_batch_id
                   OR NEW.upload_part_ordinal IS DISTINCT FROM OLD.upload_part_ordinal
                   OR NEW.role IS DISTINCT FROM OLD.role
                   OR NEW.storage_provider IS DISTINCT FROM OLD.storage_provider
                   OR NEW.object_key IS DISTINCT FROM OLD.object_key
                   OR NEW.sha256 IS DISTINCT FROM OLD.sha256
                   OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
                   OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
                   OR NEW.width IS DISTINCT FROM OLD.width
                   OR NEW.height IS DISTINCT FROM OLD.height
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'immutable media asset facts cannot change'
                        USING ERRCODE = '23514';
                END IF;

                IF OLD.read_revoked_at IS NOT NULL
                   AND NEW.read_revoked_at IS DISTINCT FROM OLD.read_revoked_at THEN
                    RAISE EXCEPTION 'media read revocation cannot move backward'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.deleted_at IS NOT NULL
                   AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                    RAISE EXCEPTION 'media deletion timestamp is immutable'
                        USING ERRCODE = '23514';
                END IF;

                IF NEW.status IS DISTINCT FROM OLD.status
                   AND NOT (
                       (OLD.status = 'PENDING_UPLOAD'
                           AND NEW.status IN ('ACTIVE', 'UPLOAD_FAILED'))
                       OR (OLD.status = 'UPLOAD_FAILED'
                           AND NEW.status IN ('PENDING_DELETE', 'QUARANTINED'))
                       OR (OLD.status = 'ACTIVE'
                           AND NEW.status IN ('PENDING_DELETE', 'QUARANTINED'))
                       OR (OLD.status = 'QUARANTINED'
                           AND NEW.status = 'PENDING_DELETE')
                       OR (OLD.status = 'PENDING_DELETE'
                           AND NEW.status IN ('DELETED', 'DELETE_FAILED'))
                       OR (OLD.status = 'DELETE_FAILED'
                           AND NEW.status = 'PENDING_DELETE')
                   ) THEN
                    RAISE EXCEPTION 'invalid media asset status transition'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status = 'DELETED' AND NEW.deleted_at IS NULL THEN
                    RAISE EXCEPTION 'deleted media asset requires deleted_at'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $media_asset_update_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_media_assets_guard_update "
            "BEFORE UPDATE ON public.media_assets FOR EACH ROW "
            "EXECUTE FUNCTION public.guard_media_asset_update()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.reject_media_asset_delete()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $media_asset_delete_guard$
            BEGIN
                RAISE EXCEPTION 'media asset rows are retained after object deletion'
                    USING ERRCODE = '23514';
            END;
            $media_asset_delete_guard$;
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_media_assets_reject_delete "
            "BEFORE DELETE ON public.media_assets FOR EACH ROW "
            "EXECUTE FUNCTION public.reject_media_asset_delete()"
        )
    )


def _create_media_rls() -> None:
    _create_nonlogin_media_role()
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO vowpic_media_service"))

    for table_name in MEDIA_TABLES:
        op.execute(sa.text(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC"))
        for role_name in ("authenticated", "vowpic_runtime", "vowpic_control_writer"):
            if _role_exists(role_name):
                op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table_name} FROM {role_name}"))
        op.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table_name} "
                "TO vowpic_media_service"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY {table_name}_media_service_all ON public.{table_name} "
                "FOR ALL TO vowpic_media_service USING (true) WITH CHECK (true)"
            )
        )

    if _role_exists("authenticated"):
        op.execute(
            sa.text(
                "GRANT SELECT (id, owner_user_id, order_id, parent_asset_id, role, "
                "mime_type, byte_size, width, height, access_level, policy_version, "
                "expires_at, status, created_at, updated_at) "
                "ON TABLE public.media_assets TO authenticated"
            )
        )
        op.execute(
            sa.text(
                "CREATE POLICY media_assets_select_own_active ON public.media_assets "
                "FOR SELECT TO authenticated USING ("
                "owner_user_id = public.app_current_user_id() "
                "AND status = 'ACTIVE' AND read_revoked_at IS NULL)"
            )
        )


def upgrade() -> None:
    _create_tables()
    _add_asset_reference_columns()
    _create_media_guards()
    _create_media_rls()


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_media_assets_reject_delete ON public.media_assets"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.reject_media_asset_delete()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_media_assets_guard_update ON public.media_assets"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.guard_media_asset_update()"))

    op.drop_index("ix_live_portrait_jobs_video_asset_id", table_name="live_portrait_jobs")
    op.drop_index("ix_live_portrait_jobs_source_asset_id", table_name="live_portrait_jobs")
    op.drop_constraint(
        "fk_live_portrait_jobs_video_asset",
        "live_portrait_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_live_portrait_jobs_source_asset",
        "live_portrait_jobs",
        type_="foreignkey",
    )
    op.drop_column("live_portrait_jobs", "video_asset_id")
    op.drop_column("live_portrait_jobs", "source_asset_id")

    for column_name in ("final_asset_ids", "preview_asset_ids", "source_asset_ids"):
        op.drop_constraint(
            f"ck_orders_{column_name}_array",
            "orders",
            type_="check",
        )
        op.drop_column("orders", column_name)

    for table_name in (
        "asset_access_grants",
        "upload_quota_reservations",
        "upload_quota_states",
        "upload_quota_windows",
        "media_assets",
        "upload_batches",
    ):
        op.drop_table(table_name)

    if _role_exists("vowpic_media_service"):
        op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM vowpic_media_service"))
