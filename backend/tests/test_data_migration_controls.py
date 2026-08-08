"""Task-28 durable checkpoint and reusable-workflow safety contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))


class _MappingRows:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class DataMigrationControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.controls = importlib.import_module(
            "app.services.data_migration_checkpoint_service"
        )

    def _contract(self, **overrides):
        values = {
            "parent_run_id": UUID("11111111-1111-4111-8111-111111111111"),
            "script_run_id": "parent:identities:write",
            "mode": "write",
            "script_sha256": "a" * 64,
            "inventory_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "source_revision": "20260710_0020",
            "approval": "approval-1",
            "lease_owner": "github-100-1-identities",
        }
        values.update(overrides)
        return self.controls.MigrationContract(**values)

    def test_contract_is_content_bound_and_child_ids_are_script_specific(self) -> None:
        first = self._contract()
        second = self._contract(script_run_id="parent:identities:dry", mode="dry")
        self.controls.validate_contract(first)
        self.controls.validate_contract(second)
        self.assertNotEqual(first.child_run_id, second.child_run_id)
        for field in (
            "script_sha256",
            "inventory_sha256",
            "manifest_sha256",
            "runtime_bundle_id",
            "source_revision",
            "approval",
            "lease_owner",
        ):
            self.assertTrue(getattr(first, field))

    def test_database_identity_matches_supabase_direct_and_pooler_roles(
        self,
    ) -> None:
        inventory = importlib.import_module(
            "scripts.release.inventory_production"
        )
        direct = (
            "postgresql://vowpic_inventory_login:secret@"
            "db.projectref123.supabase.co:5432/postgres"
        )
        pooled_inventory = (
            "postgresql://vowpic_inventory_login.projectref123:secret@"
            "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
        )
        pooled_migration = (
            "postgresql://vowpic_migration_login.projectref123:secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres"
        )
        other_project = pooled_migration.replace(
            "projectref123",
            "otherproject456",
        )
        self.assertEqual(
            inventory.source_database_identity(direct),
            inventory.source_database_identity(pooled_inventory),
        )
        self.assertEqual(
            inventory.source_database_identity(direct),
            inventory.source_database_identity(pooled_migration),
        )
        self.assertNotEqual(
            inventory.source_database_identity(direct),
            inventory.source_database_identity(other_project),
        )
        with self.assertRaisesRegex(ValueError, "project reference"):
            inventory.source_database_identity(
                "postgresql://role:secret@"
                "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
            )

    def test_invalid_mode_hash_and_counts_fail_closed(self) -> None:
        with self.assertRaises(self.controls.MigrationControlError):
            self.controls.validate_contract(self._contract(mode="deploy"))
        with self.assertRaises(self.controls.MigrationControlError):
            self.controls.validate_contract(
                self._contract(script_sha256="not-a-hash")
            )
        self.assertEqual(
            self.controls.sanitize_counts({"rows": 2, "blocked": 0}),
            {"blocked": 0, "rows": 2},
        )
        for invalid in ({"rows": -1}, {"rows": True}, {"raw email": 1}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(self.controls.MigrationControlError):
                    self.controls.sanitize_counts(invalid)

    def test_expired_parent_lease_stops_before_child_or_write(self) -> None:
        now = datetime(2026, 7, 18, tzinfo=timezone.utc)
        parent = {
            "parent_run_id": None,
            "environment": "production",
            "state": "RUNNING",
            "lease_expires_at": now - timedelta(seconds=1),
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "manifest_sha256": "c" * 64,
            "approval": "approval-1",
            "fencing_token": 1,
            "release_activation_id": UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
        }
        db = AsyncMock()
        db.execute.side_effect = [
            object(),
            _MappingRows(parent),
        ]
        service = self.controls.DataMigrationCheckpointService(db)

        async def run():
            await service.lock_contract(self._contract(), now=now)

        with self.assertRaisesRegex(
            self.controls.MigrationControlError,
            "lease has expired",
        ):
            import asyncio

            asyncio.run(run())
        self.assertEqual(db.execute.await_count, 2)

    def test_existing_child_rejects_cross_contract_resume_and_stale_fence(
        self,
    ) -> None:
        now = datetime(2026, 7, 18, tzinfo=timezone.utc)
        contract = self._contract()
        parent = {
            "parent_run_id": None,
            "environment": "production",
            "state": "RUNNING",
            "lease_expires_at": now + timedelta(minutes=10),
            "runtime_bundle_id": contract.runtime_bundle_id,
            "manifest_sha256": contract.manifest_sha256,
            "approval": contract.approval,
            "fencing_token": 7,
            "release_activation_id": UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
        }
        child = {
            "state": "RUNNING",
            "lease_expires_at": now + timedelta(minutes=10),
            "parent_run_id": contract.parent_run_id,
            "release_activation_id": parent["release_activation_id"],
            "environment": "production",
            "runtime_bundle_id": contract.runtime_bundle_id,
            "manifest_sha256": contract.manifest_sha256,
            "inventory_sha256": contract.inventory_sha256,
            "script_sha256": contract.script_sha256,
            "source_revision": contract.source_revision,
            "mode": contract.mode,
            "approval": contract.approval,
            "lease_owner": contract.lease_owner,
            "fencing_token": parent["fencing_token"],
        }
        for field, drifted in (
            ("mode", "dry"),
            ("manifest_sha256", "f" * 64),
            ("script_sha256", "e" * 64),
            ("fencing_token", 6),
        ):
            with self.subTest(field=field):
                db = AsyncMock()
                db.execute.side_effect = [
                    object(),
                    _MappingRows(parent),
                    _MappingRows({**child, field: drifted}),
                ]
                service = self.controls.DataMigrationCheckpointService(db)

                async def run():
                    await service.lock_contract(contract, now=now)

                with self.assertRaisesRegex(
                    self.controls.MigrationControlError,
                    "contract drift",
                ):
                    import asyncio

                    asyncio.run(run())

    def test_inventory_signature_is_canonical_fresh_and_read_only_proven(
        self,
    ) -> None:
        verifier = importlib.import_module(
            "scripts.release.verify_inventory_signature"
        )
        now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        payload = {
            "schema": "vowpic.production-inventory.v2",
            "generated_at": now.isoformat(),
            "schema_revision": "20260710_0020",
            "source_database_identity_hmac_sha256": "a" * 64,
            "users": {"total": 1},
            "ledger": {},
            "orders": {},
            "objects": {},
            "conflict_group_hmacs": {},
            "url_inventory_hmac_sha256": "b" * 64,
            "read_only_proof": {
                "authenticated_role_name": "vowpic_inventory_login",
                "role_name": "vowpic_inventory_login",
                "transaction_read_only": True,
                "default_transaction_read_only": True,
                "role_superuser": False,
                "role_create_db": False,
                "role_create_role": False,
                "role_replication": False,
                "role_bypass_rls": False,
                "role_membership_count": 0,
                "owned_object_count": 0,
                "inventory_table_count": 12,
                "readable_inventory_table_count": 12,
                "inventory_sequence_count": 2,
                "readable_inventory_sequence_count": 2,
                "rls_table_count": 4,
                "inventory_select_policy_count": 4,
                "invalid_inventory_policy_count": 0,
                "writable_table_count": 0,
                "write_probe_sqlstate": "25006",
            },
        }
        raw = verifier.canonical_json_bytes(payload)
        key = b"k" * 32
        signature = (
            "hmac-sha256:"
            + hmac.new(key, raw, hashlib.sha256).hexdigest()
            + "\n"
        )
        tmp_root = ROOT / ".tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as directory:
            report = Path(directory) / "inventory.json"
            detached = Path(directory) / "inventory.sig"
            report.write_bytes(raw)
            detached.write_text(signature, encoding="ascii")
            verified = verifier.verify_inventory_evidence(
                report_path=report,
                signature_path=detached,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                hmac_key=key,
                now=now,
            )
            self.assertEqual(verified["schema_revision"], "20260710_0020")
            with self.assertRaisesRegex(ValueError, "stale"):
                verifier.verify_inventory_evidence(
                    report_path=report,
                    signature_path=detached,
                    expected_sha256=hashlib.sha256(raw).hexdigest(),
                    hmac_key=key,
                    now=now + timedelta(hours=1),
                )
            detached.write_text(
                "hmac-sha256:" + "0" * 64 + "\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(ValueError, "signature mismatch"):
                verifier.verify_inventory_evidence(
                    report_path=report,
                    signature_path=detached,
                    expected_sha256=hashlib.sha256(raw).hexdigest(),
                    hmac_key=key,
                    now=now,
                )

    def test_write_blocker_rolls_back_before_checkpoint_advance(self) -> None:
        source = (
            ROOT / "scripts/release/_migration_common.py"
        ).read_text(encoding="utf-8")
        blocker = source.index("if invocation.write and batch_blockers:")
        raised = source.index("raise _BlockedWriteBatch", blocker)
        checkpoint = source.index("await service.checkpoint", raised)
        self.assertLess(raised, checkpoint)
        self.assertIn("blocked_rows_rolled_back", source)

    def test_every_batch_revalidates_evidence_and_live_schema_revision(
        self,
    ) -> None:
        source = (
            ROOT / "scripts/release/_migration_common.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("_revalidate_batch_evidence(invocation)"),
            2,
        )
        self.assertGreaterEqual(source.count("require_source_revision(db)"), 3)
        self.assertIn(
            "migration script changed after invocation binding",
            source,
        )

    def test_additive_entrypoint_is_exact_and_never_downgrades(self) -> None:
        source = (
            ROOT / "scripts/release/apply_additive_migrations.py"
        ).read_text(encoding="utf-8")
        self.assertIn('TARGET = "20260710_0021"', source)
        self.assertIn('"20260710_0021_google_auth_only_activation.py"', source)
        self.assertIn('"upgrade",\n                    TARGET', source)
        self.assertIn("lock_timeout=5s", source)
        self.assertIn("statement_timeout=1800s", source)
        self.assertNotIn('"downgrade"', source)
        self.assertIn('"automatic_downgrade": False', source)
        self.assertIn(
            "await repair_commercial_guard_row_shapes(db)",
            source,
        )
        self.assertIn('"commercial_guard_repair": guard_repair_report', source)
        self.assertIn(
            "additional_script_paths=(GUARD_REPAIR_PATH,)",
            source,
        )
        self.assertIn('"control_checksums": controls', source)
        self.assertIn("SQLAlchemyError", source)

    def test_additive_contract_hash_binds_the_guard_repair_dependency(
        self,
    ) -> None:
        common = importlib.import_module("scripts.release._migration_common")
        apply_script = ROOT / "scripts/release/apply_additive_migrations.py"
        guard_repair = (
            ROOT / "scripts/release/repair_commercial_guard_row_shapes.py"
        )
        self.assertNotEqual(
            common._bound_script_sha256((apply_script, guard_repair)),
            common._bound_script_sha256((apply_script,)),
        )

    def test_raw_probe_material_is_ephemeral_mode_0600_and_create_once(
        self,
    ) -> None:
        exporter = (
            ROOT / "scripts/release/export_legacy_url_probe_manifest.py"
        ).read_text(encoding="utf-8")
        verifier = (
            ROOT / "scripts/release/verify_legacy_url_invalidation.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('os.environ.get("RUNNER_TEMP"', exporter)
        self.assertIn("os.O_EXCL", exporter)
        self.assertIn("0o600", exporter)
        self.assertIn("workspace in output.parents", exporter)
        self.assertIn('flag: "wx"', verifier)
        self.assertIn("await unlink(manifestPath)", verifier)

    def test_legacy_url_probe_entrypoint_exposes_help_without_credentials(
        self,
    ) -> None:
        completed = subprocess.run(
            [
                "node",
                str(
                    ROOT
                    / "scripts/release/verify_legacy_url_invalidation.mjs"
                ),
                "--help",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--probe-manifest PATH", completed.stdout)
        self.assertNotIn("ERROR:", completed.stderr)

    def test_reusable_workflow_has_only_authenticated_workflow_call(self) -> None:
        workflow_path = ROOT / ".github/workflows/data-migration.yml"
        self.assertTrue(workflow_path.exists(), "data-migration.yml is missing")
        source = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", source)
        for forbidden in (
            "workflow_dispatch:",
            "repository_dispatch:",
            "pull_request:",
            "schedule:",
            "\n  push:",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("GITHUB_WORKFLOW_REF", source)
        self.assertIn(
            ".github/workflows/production-release.yml@refs/heads/main",
            source,
        )
        self.assertIn("GITHUB_WORKFLOW_SHA", source)
        self.assertIn("github.run_id", source)
        self.assertIn("github.run_attempt", source)
        self.assertIn("environment: production", source)
        self.assertNotIn("group: vowpic-production-release", source)
        production_workflow = (
            ROOT / ".github/workflows/production-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("group: vowpic-production-release", production_workflow)
        self.assertIn("cancel-in-progress: false", production_workflow)
        self.assertIn("id: validated-report", source)
        self.assertIn(
            "steps.validated-report.outputs.sanitized == 'true'",
            source,
        )
        self.assertIn(
            'echo "sanitized=true" >> "$GITHUB_OUTPUT"',
            source,
        )
        self.assertIn(
            "EXPECTED_MEDIA_DELETE_DRY_SHA256",
            source,
        )
        delete_step = source[source.index(
            "Delete old public bytes with public-delete and private-read only"
        ):source.index("Validate sanitized create-once report")]
        self.assertIn(
            "EXPECTED_MEDIA_DELETE_DRY_SHA256: "
            "${{ inputs.expected_media_delete_dry_sha256 }}",
            delete_step,
        )
        self.assertIn(
            '--expected-dry-run-sha256 "$EXPECTED_MEDIA_DELETE_DRY_SHA256"',
            delete_step,
        )
        self.assertNotIn('DRY_SHA="$(sha256sum', source)
        guard = source.index("Verify authenticated caller and exact main source")
        first_secret = source.index("PRODUCTION_MIGRATION_DATABASE_URL:")
        self.assertLess(guard, first_secret)


if __name__ == "__main__":
    unittest.main()
