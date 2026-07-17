"""Isolated backup/restore rehearsal safety and cleanup contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "backup_restore_rehearsal.py"


def _module():
    if not SCRIPT.exists():
        raise AssertionError("backup/restore rehearsal tool is missing")
    spec = importlib.util.spec_from_file_location("backup_restore_rehearsal", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(module, artifact_dir: Path, **overrides):
    values = {
        "source_url": "postgresql://inventory:source-only@localhost:5432/ai_wedding",
        "target_url": "postgresql://vowpic_restore_role:restore-only@localhost:5432/vowpic_restore_test",
        "target_admin_url": "postgresql://postgres:admin-only@localhost:5432/postgres",
        "target_role_name": "vowpic_restore_role",
        "artifact_dir": artifact_dir,
        "scratch_dir": artifact_dir.parent / f"{artifact_dir.name}-scratch",
        "expected_target_db_prefix": "vowpic_restore_",
        "target_credential_expires_at": None,
    }
    values.update(overrides)
    return module.RehearsalConfig.from_urls(**values)


class RehearsalValidationTest(unittest.TestCase):
    def test_legacy_url_queries_skip_columns_absent_before_0013(self) -> None:
        module = _module()
        queries = module.build_scalar_reference_queries(
            {
                "users": {"id", "openid"},
                "live_portrait_jobs": {"source_image_url"},
                "credit_purchases": {"id", "user_id"},
            }
        )
        rendered = "\n".join(statement for _, statement in queries)
        self.assertNotIn("avatar_url", rendered)
        self.assertNotIn("video_url", rendered)
        self.assertNotIn("checkout_url", rendered)
        self.assertIn("source_image_url", rendered)

    def test_rejects_source_target_alias_and_wrong_target_prefix(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            with self.assertRaises(ValueError):
                _config(
                    module,
                    artifact_dir,
                    target_url="postgresql://other:password@localhost:5432/ai_wedding",
                )
            with self.assertRaises(ValueError):
                _config(
                    module,
                    artifact_dir,
                    target_url="postgresql://other:password@localhost:5432/shared_database",
                )

    def test_nonlocal_target_requires_short_lived_credential(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            remote_url = (
                "postgresql://vowpic_restore_role:password@restore.internal:5432/"
                "vowpic_restore_test?sslmode=require"
            )
            remote_admin = "postgresql://postgres:admin@restore.internal:5432/postgres?sslmode=require"
            with self.assertRaises(ValueError):
                _config(module, artifact_dir, target_url=remote_url, target_admin_url=remote_admin)
            expires = datetime.now(timezone.utc) + timedelta(minutes=30)
            config = _config(
                module,
                artifact_dir,
                target_url=remote_url,
                target_admin_url=remote_admin,
                target_credential_expires_at=expires,
            )
            self.assertEqual(config.target.database, "vowpic_restore_test")
            with self.assertRaises(ValueError):
                _config(
                    module,
                    artifact_dir,
                    target_url=(
                        "postgresql://vowpic_restore_role:password@restore.example.com:5432/"
                        "vowpic_restore_test?sslmode=require"
                    ),
                    target_admin_url=(
                        "postgresql://postgres:admin@restore.example.com:5432/postgres?sslmode=require"
                    ),
                    target_credential_expires_at=expires,
                )

    def test_internal_hostname_is_rejected_when_dns_resolves_to_a_public_address(self) -> None:
        module = _module()
        connection = module.DatabaseConnection.from_url(
            "postgresql://vowpic_restore_role:password@restore.internal:5432/"
            "vowpic_restore_test?sslmode=require"
        )

        def public_resolver(_host, _port, *, type):
            self.assertIsNotNone(type)
            return [(2, 1, 6, "", ("8.8.8.8", 5432))]

        with self.assertRaises(module.RehearsalError):
            module.resolve_private_target_addresses(connection, resolver=public_resolver)

    def test_target_role_expiry_and_privilege_are_verified_from_postgresql_facts(self) -> None:
        module = _module()
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        expires = now + timedelta(minutes=30)
        valid = {
            "database": "vowpic_restore_test",
            "database_owner": "vowpic_restore_role",
            "role": "vowpic_restore_role",
            "role_valid_until": expires,
            "role_superuser": False,
            "role_create_db": False,
            "role_create_role": False,
            "role_replication": False,
            "role_bypass_rls": False,
            "privileged_membership_count": 0,
            "server_address": "10.20.30.40",
        }
        proof = module.validate_target_control_proof(
            valid,
            expected_database="vowpic_restore_test",
            expected_role="vowpic_restore_role",
            expected_expires_at=expires,
            resolved_addresses={"10.20.30.40"},
            now=now,
            local_target=False,
        )
        self.assertTrue(proof["database_owner_matches"])
        self.assertTrue(proof["credential_expiry_matches"])
        self.assertRegex(proof["server_address_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("server_address", proof)

        for override in (
            {"role_valid_until": expires + timedelta(minutes=5)},
            {"role_bypass_rls": True},
            {"server_address": "8.8.8.8"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(module.RehearsalError):
                    module.validate_target_control_proof(
                        {**valid, **override},
                        expected_database="vowpic_restore_test",
                        expected_role="vowpic_restore_role",
                        expected_expires_at=expires,
                        resolved_addresses={str(override.get("server_address", "10.20.30.40"))},
                        now=now,
                        local_target=False,
                    )

    def test_postgres_inet_server_addresses_are_normalized_before_comparison(self) -> None:
        module = _module()
        now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        proof = module.validate_target_control_proof(
            {
                "database": "vowpic_restore_test",
                "database_owner": "vowpic_restore_role",
                "role": "vowpic_restore_role",
                "role_valid_until": None,
                "role_superuser": False,
                "role_create_db": False,
                "role_create_role": False,
                "role_replication": False,
                "role_bypass_rls": False,
                "privileged_membership_count": 0,
                "server_address": "127.0.0.1/32",
                "admin_server_address": "127.0.0.1/32",
            },
            expected_database="vowpic_restore_test",
            expected_role="vowpic_restore_role",
            expected_expires_at=None,
            resolved_addresses={"127.0.0.1"},
            now=now,
            local_target=True,
        )
        self.assertTrue(proof["network_isolated"])
        self.assertEqual(proof["resolved_address_count"], 1)

    def test_dump_and_restore_commands_never_contain_passwords_or_urls(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            config = _config(module, Path(directory))
            dump = module.build_pg_dump_invocation(config, config.archive_path)
            restore = module.build_pg_restore_invocation(config, config.archive_path)
            rendered = " ".join([*dump.argv, *restore.argv])
            self.assertNotIn("source-only", rendered)
            self.assertNotIn("restore-only", rendered)
            self.assertNotIn("postgresql://", rendered)
            self.assertEqual(dump.env["PGPASSWORD"], "source-only")
            self.assertIn("--enable-row-security", dump.argv)
            self.assertIn("--schema=public", dump.argv)
            self.assertEqual(restore.env["PGPASSWORD"], "admin-only")
            self.assertEqual(restore.argv[restore.argv.index("--username") + 1], "postgres")
            self.assertEqual(
                restore.argv[restore.argv.index("--dbname") + 1],
                "vowpic_restore_test",
            )
            self.assertNotIn("source-only", dump.redacted())
            self.assertNotIn("restore-only", restore.redacted())

    def test_source_and_target_snapshots_use_inventory_and_isolated_admin_roles(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            config = _config(module, Path(directory))
            with patch.object(
                module,
                "_database_snapshot",
                side_effect=[
                    {
                        "schema_revision": "20260516_0012",
                        "tables": ["users"],
                        "row_counts": {"users": 1},
                        "fk_orphans": 0,
                        "ledger_mismatch_users": 0,
                        "url_inventory_sha256": "a" * 64,
                    },
                    {
                        "schema_revision": "20260516_0012",
                        "tables": ["users"],
                        "row_counts": {"users": 1},
                        "fk_orphans": 0,
                        "ledger_mismatch_users": 0,
                        "url_inventory_sha256": "a" * 64,
                    },
                ],
            ) as snapshot:
                comparison = module.compare_source_and_target(config)

            self.assertTrue(comparison["matches"])
            self.assertIs(snapshot.call_args_list[0].args[0], config.source)
            target_admin = snapshot.call_args_list[1].args[0]
            self.assertEqual(target_admin.username, "postgres")
            self.assertEqual(target_admin.password, "admin-only")
            self.assertEqual(target_admin.database, "vowpic_restore_test")

    def test_raw_dump_is_never_created_under_the_upload_artifact_directory(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifacts" / "security-baseline" / "restore"
            scratch_dir = root / "runner-temp" / "restore"
            config = _config(module, artifact_dir, scratch_dir=scratch_dir)
            self.assertEqual(config.archive_path.parent, scratch_dir.resolve())
            self.assertFalse(config.archive_path.is_relative_to(artifact_dir.resolve()))
            self.assertEqual(config.report_path.parent, artifact_dir.resolve())

    def test_cli_missing_protected_inputs_is_not_run_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "restore"
            env = dict(os.environ)
            names = (
                "VOWPIC_TEST_MISSING_SOURCE_URL",
                "VOWPIC_TEST_MISSING_TARGET_URL",
                "VOWPIC_TEST_MISSING_ADMIN_URL",
                "VOWPIC_TEST_MISSING_TARGET_ROLE",
            )
            for name in names:
                env.pop(name, None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source-url-env",
                    names[0],
                    "--target-url-env",
                    names[1],
                    "--target-admin-url-env",
                    names[2],
                    "--target-role-name-env",
                    names[3],
                    "--artifact-dir",
                    str(artifact_dir),
                    "--scratch-dir",
                    str(Path(directory) / "runner-temp"),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 3)
            self.assertIn("NOT_RUN", completed.stderr)
            self.assertFalse(artifact_dir.exists())


class RehearsalCleanupTest(unittest.TestCase):
    def _run_case(self, *, command_side_effect=None, comparison=None, cleanup_side_effect=None):
        module = _module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config = _config(module, Path(temporary.name))
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        source_proof = {
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
            "inventory_table_count": 20,
            "readable_inventory_table_count": 20,
            "inventory_sequence_count": 2,
            "readable_inventory_sequence_count": 2,
            "rls_table_count": 8,
            "inventory_select_policy_count": 8,
            "invalid_inventory_policy_count": 0,
            "writable_table_count": 0,
            "write_probe_sqlstate": "25006",
        }
        target_proof = {
            "database_owner_matches": True,
            "credential_expiry_matches": True,
            "network_isolated": True,
            "resolved_address_count": 1,
            "server_address_sha256": "c" * 64,
            "role_nobypassrls": True,
            "role_unprivileged": True,
        }
        comparison = comparison or {
            "matches": True,
            "schema_revision": "20260710_0013",
            "table_count": 20,
            "row_counts_sha256": "a" * 64,
            "url_inventory_sha256": "b" * 64,
            "fk_orphans": 0,
            "ledger_mismatch_users": 0,
        }
        effects = iter(command_side_effect or [None, None])

        def fake_run(invocation):
            effect = next(effects, None)
            if isinstance(effect, Exception):
                raise effect
            if invocation.argv[0] == config.pg_dump_executable:
                config.archive_path.write_bytes(b"temporary-dump")

        with (
            patch.object(module, "verify_source_read_only", return_value=source_proof),
            patch.object(module, "verify_target_controls", return_value=target_proof) as target_controls,
            patch.object(module, "prepare_restore_policy_roles", return_value=()),
            patch.object(module, "run_invocation", side_effect=fake_run),
            patch.object(module, "compare_source_and_target", return_value=comparison),
            patch.object(
                module,
                "cleanup_target",
                side_effect=cleanup_side_effect,
                return_value={"database_dropped": True, "role_dropped": True},
            ) as cleanup,
        ):
            if cleanup_side_effect:
                expected = module.CleanupFailureError
            elif command_side_effect or not comparison["matches"]:
                expected = module.RehearsalError
            else:
                expected = None
            if expected:
                with self.assertRaises(expected):
                    module.run_backup_restore_rehearsal(config)
            else:
                report = module.run_backup_restore_rehearsal(config)
                self.assertTrue(report["passed"])
                for key, value in target_proof.items():
                    self.assertEqual(report["target_controls"][key], value)
                self.assertEqual(report["target_controls"]["policy_placeholder_role_count"], 0)
                self.assertTrue(config.report_path.exists())
            target_controls.assert_called_once_with(config)
            cleanup.assert_called_once_with(config, created_policy_roles=())
        self.assertFalse(config.archive_path.exists())

    def test_cleanup_runs_after_success(self) -> None:
        self._run_case()

    def test_cleanup_runs_after_restore_failure(self) -> None:
        module = _module()
        self._run_case(command_side_effect=[None, module.CommandExecutionError("restore failed")])

    def test_cleanup_runs_after_comparison_failure(self) -> None:
        comparison = {
            "matches": False,
            "schema_revision": "mismatch",
            "table_count": 0,
            "row_counts_sha256": "a" * 64,
            "url_inventory_sha256": "b" * 64,
            "fk_orphans": 1,
            "ledger_mismatch_users": 1,
        }
        self._run_case(comparison=comparison)

    def test_cleanup_failure_overrides_success_and_exits_failed(self) -> None:
        self._run_case(cleanup_side_effect=RuntimeError("drop role failed"))

    def test_cleanup_runs_when_artifact_directory_creation_fails(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            config = _config(module, Path(directory) / "evidence")
            path_type = type(config.artifact_dir)
            with (
                patch.object(path_type, "mkdir", side_effect=PermissionError("artifact denied")),
                patch.object(module, "cleanup_target", return_value={"database_dropped": True, "role_dropped": True}) as cleanup,
            ):
                with self.assertRaises(module.RehearsalError):
                    module.run_backup_restore_rehearsal(config)
            cleanup.assert_called_once_with(config, created_policy_roles=())

    def test_cleanup_runs_when_scratch_directory_creation_fails(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            config = _config(module, Path(directory) / "evidence")
            path_type = type(config.artifact_dir)
            with (
                patch.object(path_type, "mkdir", side_effect=[None, PermissionError("scratch denied")]),
                patch.object(module, "cleanup_target", return_value={"database_dropped": True, "role_dropped": True}) as cleanup,
            ):
                with self.assertRaises(module.RehearsalError):
                    module.run_backup_restore_rehearsal(config)
            cleanup.assert_called_once_with(config, created_policy_roles=())


if __name__ == "__main__":
    unittest.main()
