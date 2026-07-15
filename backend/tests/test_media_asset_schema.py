"""Private media authority schema contract tests."""

from pathlib import Path
import unittest

from app.models.asset_access_grant import AssetAccessGrant
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.upload_batch import UploadBatch
from app.models.upload_quota_reservation import (
    UploadQuotaReservation,
    UploadQuotaReservationStatus,
)
from app.models.upload_quota_state import UploadQuotaState
from app.models.upload_quota_window import UploadQuotaWindow


ROOT_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT_DIR
    / "backend"
    / "alembic"
    / "versions"
    / "20260710_0015_private_media_assets.py"
)


def _foreign_key_on(model: type, column_name: str) -> object:
    foreign_keys = list(model.__table__.c[column_name].foreign_keys)
    if len(foreign_keys) != 1:
        raise AssertionError(f"expected exactly one foreign key on {model.__name__}.{column_name}")
    return foreign_keys[0]


class MediaAssetSchemaTest(unittest.TestCase):
    def test_upload_failure_has_one_state_at_a_time(self) -> None:
        self.assertEqual(MediaAssetStatus.PENDING_UPLOAD.value, "PENDING_UPLOAD")
        self.assertEqual(MediaAssetStatus.UPLOAD_FAILED.value, "UPLOAD_FAILED")
        self.assertNotEqual(MediaAssetStatus.UPLOAD_FAILED, MediaAssetStatus.PENDING_DELETE)

    def test_required_roles_and_statuses_are_exact(self) -> None:
        self.assertEqual(
            {role.value for role in MediaAssetRole},
            {
                "source",
                "intermediate",
                "candidate",
                "qa_input",
                "preview_watermarked",
                "final_master",
                "delivery_variant",
                "legacy_video",
            },
        )
        self.assertEqual(
            {status.value for status in MediaAssetStatus},
            {
                "PENDING_UPLOAD",
                "UPLOAD_FAILED",
                "ACTIVE",
                "PENDING_DELETE",
                "DELETE_FAILED",
                "DELETED",
                "QUARANTINED",
            },
        )

    def test_media_asset_transitions_are_explicit_and_fail_closed(self) -> None:
        pending = MediaAsset(status=MediaAssetStatus.PENDING_UPLOAD)
        self.assertTrue(pending.can_transition_to(MediaAssetStatus.ACTIVE))
        self.assertTrue(pending.can_transition_to(MediaAssetStatus.UPLOAD_FAILED))
        self.assertFalse(pending.can_transition_to(MediaAssetStatus.DELETED))

        failed = MediaAsset(status=MediaAssetStatus.DELETE_FAILED)
        self.assertTrue(failed.can_transition_to(MediaAssetStatus.PENDING_DELETE))
        self.assertFalse(failed.can_transition_to(MediaAssetStatus.ACTIVE))

        deleted = MediaAsset(status=MediaAssetStatus.DELETED)
        self.assertFalse(deleted.can_transition_to(MediaAssetStatus.ACTIVE))

    def test_media_asset_contains_only_private_authority_and_deletion_facts(self) -> None:
        columns = set(MediaAsset.__table__.c.keys())
        self.assertTrue(
            {
                "owner_user_id",
                "upload_batch_id",
                "upload_part_ordinal",
                "order_id",
                "job_id",
                "parent_asset_id",
                "role",
                "storage_provider",
                "object_key",
                "sha256",
                "mime_type",
                "byte_size",
                "width",
                "height",
                "access_level",
                "policy_version",
                "expires_at",
                "status",
                "read_revoked_at",
                "delete_attempts",
                "next_delete_at",
                "last_delete_error",
                "deleted_at",
                "lease_owner",
                "lease_claim_id",
                "lease_expires_at",
                "fencing_token",
            }.issubset(columns)
        )
        self.assertFalse(any("url" in column.lower() for column in columns))

        self.assertEqual(_foreign_key_on(MediaAsset, "owner_user_id").ondelete, "RESTRICT")
        self.assertEqual(_foreign_key_on(MediaAsset, "upload_batch_id").ondelete, "RESTRICT")
        self.assertEqual(_foreign_key_on(MediaAsset, "order_id").ondelete, "RESTRICT")
        self.assertEqual(_foreign_key_on(MediaAsset, "parent_asset_id").ondelete, "RESTRICT")
        self.assertEqual(
            _foreign_key_on(MediaAsset, "job_id").target_fullname,
            "generation_jobs.id",
        )
        self.assertEqual(_foreign_key_on(MediaAsset, "job_id").ondelete, "RESTRICT")

        unique_columns = {
            tuple(constraint.columns.keys())
            for constraint in MediaAsset.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("storage_provider", "object_key"), unique_columns)
        self.assertIn(("upload_batch_id", "upload_part_ordinal"), unique_columns)

    def test_provider_grants_are_hash_only_and_prebound_for_worker_contract(self) -> None:
        columns = set(AssetAccessGrant.__table__.c.keys())
        self.assertTrue(
            {
                "asset_id",
                "token_hash",
                "provider",
                "purpose",
                "job_id",
                "attempt_id",
                "runtime_bundle_id",
                "target_api_deployment_id",
                "serving_deployment_role",
                "expires_at",
                "max_reads",
                "used_count",
                "revoked_at",
            }.issubset(columns)
        )
        self.assertFalse(any("url" in column.lower() for column in columns))
        self.assertFalse(any(column == "token" for column in columns))
        self.assertEqual(AssetAccessGrant.__table__.c.token_hash.type.length, 64)
        self.assertEqual(_foreign_key_on(AssetAccessGrant, "asset_id").ondelete, "RESTRICT")
        self.assertEqual(
            _foreign_key_on(AssetAccessGrant, "job_id").target_fullname,
            "generation_jobs.id",
        )
        self.assertEqual(
            _foreign_key_on(AssetAccessGrant, "attempt_id").target_fullname,
            "generation_attempts.id",
        )
        self.assertEqual(_foreign_key_on(AssetAccessGrant, "job_id").ondelete, "RESTRICT")
        self.assertEqual(_foreign_key_on(AssetAccessGrant, "attempt_id").ondelete, "RESTRICT")
        self.assertTrue(AssetAccessGrant.__table__.c.job_id.index)
        self.assertTrue(AssetAccessGrant.__table__.c.attempt_id.index)

    def test_quota_tables_persist_windows_slots_and_idempotent_part_settlement(self) -> None:
        self.assertTrue(
            {"user_id", "window_kind", "window_start", "request_count", "attempted_bytes", "reserved_bytes"}
            .issubset(UploadQuotaWindow.__table__.c.keys())
        )
        unique_windows = {
            tuple(constraint.columns.keys())
            for constraint in UploadQuotaWindow.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("user_id", "window_kind", "window_start"), unique_windows)

        self.assertEqual(tuple(UploadQuotaState.__table__.primary_key.columns.keys()), ("user_id",))
        self.assertTrue({"active_slots", "version"}.issubset(UploadQuotaState.__table__.c.keys()))

        reservation_columns = set(UploadQuotaReservation.__table__.c.keys())
        self.assertTrue(
            {
                "batch_id",
                "quota_window_id",
                "part_ordinal",
                "reserved_bytes",
                "actual_attempted_bytes",
                "status",
                "settled_at",
                "slot_released_at",
            }.issubset(reservation_columns)
        )
        unique_reservations = {
            tuple(constraint.columns.keys())
            for constraint in UploadQuotaReservation.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("batch_id", "part_ordinal"), unique_reservations)
        self.assertEqual(
            _foreign_key_on(UploadQuotaReservation, "quota_window_id").ondelete,
            "RESTRICT",
        )
        self.assertEqual(
            {status.value for status in UploadQuotaReservationStatus},
            {"RESERVED", "SETTLED", "RELEASED"},
        )
        counter_constraints = " ".join(
            str(constraint.sqltext).lower()
            for constraint in UploadQuotaReservation.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        )
        self.assertNotIn("actual_attempted_bytes <= reserved_bytes", counter_constraints)

    def test_upload_batch_is_owned_and_has_a_durable_slot_lease(self) -> None:
        columns = set(UploadBatch.__table__.c.keys())
        self.assertTrue(
            {
                "owner_user_id",
                "status",
                "request_id",
                "expected_files",
                "received_files",
                "expires_at",
                "lease_expires_at",
                "slot_released_at",
            }.issubset(columns)
        )
        self.assertEqual(_foreign_key_on(UploadBatch, "owner_user_id").ondelete, "RESTRICT")

    def test_expand_only_migration_contains_constraints_rls_and_asset_references(self) -> None:
        self.assertTrue(MIGRATION_PATH.exists())
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        lower = source.lower()

        self.assertIn('revision = "20260710_0015"', source)
        self.assertIn('down_revision = "20260710_0014"', source)
        for table_name in (
            "upload_batches",
            "media_assets",
            "asset_access_grants",
            "upload_quota_windows",
            "upload_quota_states",
            "upload_quota_reservations",
        ):
            self.assertIn(f'"{table_name}"', source)
        self.assertIn("public.{table_name}", lower)

        self.assertIn("enable row level security", lower)
        self.assertIn("force row level security", lower)
        self.assertIn("vowpic_media_service", lower)
        self.assertIn("app_current_user_id()", lower)
        self.assertIn("read_revoked_at", lower)
        self.assertIn("lease_claim_id", lower)
        self.assertIn("fencing_token", lower)
        self.assertIn("source_asset_id", lower)
        self.assertIn("video_asset_id", lower)
        self.assertIn("guard_media_asset_update", lower)
        self.assertIn("reject_media_asset_delete", lower)
        self.assertIn("grant select, insert, update on table public.", lower)
        self.assertNotIn(
            "grant select, insert, update, delete on table public.",
            lower,
        )

        # This migration is additive: URL compatibility columns remain until Task 30.
        self.assertNotIn('drop_column("orders", "source_image_urls"', lower)
        self.assertNotIn('drop_column("orders", "preview_image_urls"', lower)
        self.assertNotIn('drop_column("orders", "final_image_urls"', lower)
        self.assertNotIn('drop_column("live_portrait_jobs", "source_image_url"', lower)
        self.assertNotIn('drop_column("live_portrait_jobs", "video_url"', lower)


if __name__ == "__main__":
    unittest.main()
