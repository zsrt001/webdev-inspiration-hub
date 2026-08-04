from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = ROOT / "scripts" / "release"
BOOTSTRAP_SQL = RELEASE_DIR / "bootstrap_production_database_roles.sql"
OBSERVATION_BOOTSTRAP_SQL = (
    RELEASE_DIR / "bootstrap_observation_database_roles.sql"
)
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260710_0020_partner_consent.py"
)
PROOF_SCRIPT = RELEASE_DIR / "production_database_login_proof.py"
PROVISION_SCRIPT = RELEASE_DIR / "provision_observation_database_logins.py"
PROVISION_WORKFLOW = (
    ROOT / ".github" / "workflows" / "production-observation-database-logins.yml"
)

PROOF_SPEC = importlib.util.spec_from_file_location(
    "observation_database_login_proof_test",
    PROOF_SCRIPT,
)
assert PROOF_SPEC and PROOF_SPEC.loader
proof = importlib.util.module_from_spec(PROOF_SPEC)
PROOF_SPEC.loader.exec_module(proof)

PROVISION_SPEC = importlib.util.spec_from_file_location(
    "provision_observation_database_logins_test",
    PROVISION_SCRIPT,
)
assert PROVISION_SPEC and PROVISION_SPEC.loader
provision = importlib.util.module_from_spec(PROVISION_SPEC)
PROVISION_SPEC.loader.exec_module(provision)


def _observation_facts(
    *,
    role_name: str,
    group: str,
    read_only: bool,
    writer: bool,
) -> dict[str, object]:
    return {
        "current_user": role_name,
        "superuser": False,
        "inherit_privileges": True,
        "create_db": False,
        "create_role": False,
        "replication": False,
        "bypass_rls": False,
        "default_read_only": "on" if read_only else "off",
        "schema_usage": True,
        "required_group_member": True,
        "direct_memberships": [group],
        "owns_objects": False,
        "activations_select": True,
        "runs_select": True,
        "samples_select": True,
        "samples_insert": writer,
        "samples_update": False,
        "samples_delete": False,
        "runs_insert": False,
        "runs_table_update": False,
        "cleanup_hash_update": False,
        "run_version_update": False,
        "run_state_update": False,
        "runs_delete": False,
        "users_select": False,
        "flags_select": False,
        "flags_update": False,
        "recoveries_select": False,
        "metrics_execute": not writer,
    }


class ObservationDatabaseRoleContractTest(unittest.TestCase):
    def test_provisioning_requires_schema_0020_before_rotation(self) -> None:
        cursor = mock.Mock()
        cursor.fetchone.return_value = {"version_num": "20260712_0014"}

        with self.assertRaisesRegex(
            ValueError,
            "requires schema 20260710_0020; current schema is 20260712_0014",
        ):
            provision._require_observation_schema(cursor)

        cursor.execute.assert_called_once_with(
            "SELECT version_num FROM public.alembic_version"
        )

    def test_provisioning_accepts_schema_0020(self) -> None:
        cursor = mock.Mock()
        cursor.fetchone.return_value = {"version_num": "20260710_0020"}

        self.assertEqual(
            provision._require_observation_schema(cursor),
            "20260710_0020",
        )

    def test_additive_bootstrap_never_rotates_existing_production_credentials(self) -> None:
        source = OBSERVATION_BOOTSTRAP_SQL.read_text(encoding="utf-8")
        for required in (
            "vowpic.observation-role-bootstrap.v1",
            "vowpic_observation_reader",
            "vowpic_observation_writer",
            "vowpic_observation_reader_login",
            "vowpic_observation_writer_login",
            "vowpic_rotate_observation_database_logins",
            "observation database login rotation requires the migration login",
            "OWNER TO postgres",
            "FROM PUBLIC",
            "TO vowpic_migration_owner",
            "current_revision NOT IN ('20260712_0014', '20260710_0020')",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "inventory_password",
            "migration_password",
            "ALTER ROLE vowpic_inventory_login",
            "ALTER ROLE vowpic_migration_login",
            "ALTER ROLE vowpic_app_runtime",
            "ALTER ROLE vowpic_control_writer_login",
            "GRANT postgres",
        ):
            self.assertNotIn(forbidden, source)

    def test_bootstrap_precreates_and_scopes_both_observation_logins(self) -> None:
        source = BOOTSTRAP_SQL.read_text(encoding="utf-8")
        for required in (
            "vowpic_observation_reader",
            "vowpic_observation_writer",
            "vowpic_observation_reader_login",
            "vowpic_observation_writer_login",
            "vowpic_rotate_observation_database_logins",
            "reader_password text,",
            "writer_password text",
            "REVOKE vowpic_control_writer FROM vowpic_observation_reader_login",
            "REVOKE vowpic_control_writer FROM vowpic_observation_writer_login",
            "GRANT vowpic_observation_reader TO vowpic_observation_reader_login",
            "GRANT vowpic_observation_writer TO vowpic_observation_writer_login",
            "SET default_transaction_read_only = on",
            "RESET default_transaction_read_only",
            "REVOKE ALL ON FUNCTION "
            "public.vowpic_rotate_observation_database_logins(text, text)",
        ):
            self.assertIn(required, source)
        self.assertNotIn("pg_read_all_data", source)
        self.assertNotIn("pg_write_all_data", source)

    def test_schema_0020_grants_only_the_reviewed_observation_surface(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for required in (
            "GRANT SELECT ON TABLE public.{table} TO ",
            "vowpic_observation_reader, vowpic_observation_writer",
            "GRANT INSERT ON TABLE public.release_observation_samples ",
            "TO vowpic_observation_writer",
            "read_release_observation_metrics_v1(uuid)",
            "TO vowpic_observation_reader",
        ):
            self.assertIn(required, source)
        self.assertNotIn(
            "GRANT DELETE ON TABLE public.release_observation",
            source,
        )
        self.assertNotIn(
            "GRANT UPDATE (cleanup_cycle_sha256, version)",
            source,
        )

    def test_reader_and_writer_facts_are_exactly_split(self) -> None:
        reader = _observation_facts(
            role_name=proof.OBSERVATION_READER_LOGIN,
            group=proof.OBSERVATION_READER_GROUP,
            read_only=True,
            writer=False,
        )
        writer = _observation_facts(
            role_name=proof.OBSERVATION_WRITER_LOGIN,
            group=proof.OBSERVATION_WRITER_GROUP,
            read_only=False,
            writer=True,
        )

        self.assertEqual(
            proof._validate_observation_login_facts(
                reader,
                role_name=proof.OBSERVATION_READER_LOGIN,
                required_group=proof.OBSERVATION_READER_GROUP,
                expect_read_only=True,
            ),
            reader,
        )
        self.assertEqual(
            proof._validate_observation_login_facts(
                writer,
                role_name=proof.OBSERVATION_WRITER_LOGIN,
                required_group=proof.OBSERVATION_WRITER_GROUP,
                expect_read_only=False,
            ),
            writer,
        )

    def test_writer_cannot_mutate_observation_state_or_control_tables(self) -> None:
        writer = _observation_facts(
            role_name=proof.OBSERVATION_WRITER_LOGIN,
            group=proof.OBSERVATION_WRITER_GROUP,
            read_only=False,
            writer=True,
        )
        for drift in (
            {"run_state_update": True},
            {"flags_update": True},
            {"samples_delete": True},
            {"direct_memberships": [proof.OBSERVATION_WRITER_GROUP, "vowpic_control_writer"]},
        ):
            with self.subTest(drift=drift), self.assertRaisesRegex(
                ValueError,
                "least-privilege",
            ):
                proof._validate_observation_login_facts(
                    {**writer, **drift},
                    role_name=proof.OBSERVATION_WRITER_LOGIN,
                    required_group=proof.OBSERVATION_WRITER_GROUP,
                    expect_read_only=False,
                )


class ObservationDatabaseSecretPublishingTest(unittest.TestCase):
    def test_github_secrets_use_stdin_and_metadata_readback(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(args, *, input, text, capture_output, check):
            self.assertTrue(text)
            self.assertTrue(capture_output)
            self.assertFalse(check)
            calls.append((list(args), input))
            if args[1:3] == ["secret", "list"]:
                stdout = json.dumps(
                    [
                        {
                            "name": "OBSERVATION_READ_DATABASE_URL",
                            "updatedAt": "2026-07-19T00:00:00Z",
                        },
                        {
                            "name": "OBSERVATION_WRITE_DATABASE_URL",
                            "updatedAt": "2026-07-19T00:00:01Z",
                        },
                    ]
                )
            else:
                stdout = ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        reader_url = "postgresql://reader:reader-secret@db.example/postgres"
        writer_url = "postgresql://writer:writer-secret@db.example/postgres"
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "gh.exe"
            cli.touch()
            with mock.patch.object(
                provision.subprocess,
                "run",
                side_effect=fake_run,
            ):
                result = provision.publish_github_observation_database_urls(
                    github_cli=str(cli),
                    repository="zsrt001/webdev-inspiration-hub",
                    environment="production-observation",
                    reader_url=reader_url,
                    writer_url=writer_url,
                )

        self.assertEqual(
            set(result),
            {
                "OBSERVATION_READ_DATABASE_URL",
                "OBSERVATION_WRITE_DATABASE_URL",
            },
        )
        set_calls = [call for call in calls if call[0][1:3] == ["secret", "set"]]
        self.assertEqual([call[1] for call in set_calls], [reader_url, writer_url])
        for args, _ in calls:
            command = " ".join(args)
            self.assertNotIn("reader-secret", command)
            self.assertNotIn("writer-secret", command)
            self.assertIn("--env", args)
            self.assertIn("production-observation", args)

    def test_complete_release_secret_set_is_published_to_exact_environments(
        self,
    ) -> None:
        calls: list[tuple[list[str], str | None]] = []
        expected_by_environment = {
            "production-observation": {
                "OBSERVATION_READ_DATABASE_URL",
                "OBSERVATION_WRITE_DATABASE_URL",
                "OBSERVATION_SIGNING_KEY",
                "RELEASE_EVIDENCE_HMAC_KEY",
            },
            "production-observation-emergency": {
                "OBSERVATION_EMERGENCY_DATABASE_URL",
            },
            "production-recovery": {
                "PRODUCTION_MIGRATION_DATABASE_URL",
                "VERCEL_TOKEN",
                "PRODUCTION_ACCEPTANCE_APPROVAL_ID",
            },
            "production": {"OBSERVATION_SIGNING_KEY"},
        }

        def fake_run_gh(args, *, stdin=None, redact=()):
            calls.append((list(args), stdin))
            if args[1:3] != ["secret", "list"]:
                return ""
            environment = args[args.index("--env") + 1]
            return json.dumps(
                [
                    {
                        "name": name,
                        "updatedAt": "2026-08-03T00:00:00Z",
                    }
                    for name in sorted(expected_by_environment[environment])
                ]
            )

        secret_values = {
            "reader_url": "postgresql://reader:reader-secret@db.example/postgres",
            "writer_url": "postgresql://writer:writer-secret@db.example/postgres",
            "migration_url": "postgresql://migration:migration-secret@db.example/postgres",
            "vercel_token": "vercel-secret",
            "acceptance_approval_id": "approval-secret",
            "observation_signing_key": "observation-signing-secret",
            "release_evidence_hmac_key": "release-evidence-secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "gh.exe"
            cli.touch()
            with mock.patch.object(
                provision,
                "_run_gh",
                side_effect=fake_run_gh,
            ):
                result = provision.publish_github_observation_release_secrets(
                    github_cli=str(cli),
                    repository="zsrt001/webdev-inspiration-hub",
                    observation_environment="production-observation",
                    emergency_environment="production-observation-emergency",
                    recovery_environment="production-recovery",
                    production_environment="production",
                    **secret_values,
                )

        self.assertEqual(
            {environment: set(metadata) for environment, metadata in result.items()},
            expected_by_environment,
        )
        set_calls = [call for call in calls if call[0][1:3] == ["secret", "set"]]
        self.assertEqual(len(set_calls), 9)
        self.assertEqual(
            sum(
                1
                for args, value in set_calls
                if args[3] == "OBSERVATION_SIGNING_KEY"
                and value == secret_values["observation_signing_key"]
            ),
            2,
        )
        self.assertIn(
            secret_values["writer_url"],
            [value for _, value in set_calls],
        )
        for args, _ in set_calls:
            command = " ".join(args)
            for secret in secret_values.values():
                self.assertNotIn(secret, command)

    def test_protected_workflow_supplies_and_cleans_one_time_publisher(self) -> None:
        workflow = PROVISION_WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}",
            "PRODUCTION_ACCEPTANCE_APPROVAL_ID: "
            "${{ secrets.PRODUCTION_ACCEPTANCE_APPROVAL_ID }}",
            "--emergency-github-environment production-observation-emergency",
            "--recovery-github-environment production-recovery",
            "--production-github-environment production",
            "gh secret delete ONE_TIME_OBSERVATION_SECRET_PUBLISH_TOKEN",
        ):
            self.assertIn(required, workflow)
        self.assertIn("if: ${{ always() }}", workflow)


if __name__ == "__main__":
    unittest.main()
