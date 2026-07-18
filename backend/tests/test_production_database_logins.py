from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "provision_production_database_logins.py"
BOOTSTRAP_SQL = ROOT / "scripts" / "release" / "bootstrap_production_database_roles.sql"
SPEC = importlib.util.spec_from_file_location("provision_production_database_logins", SCRIPT)
assert SPEC and SPEC.loader
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)


class ProductionDatabaseLoginUrlTest(unittest.TestCase):
    def test_role_contract_is_exact_and_forbids_destructive_business_privileges(self) -> None:
        privileges = provision.load_database_role_contract()
        self.assertEqual(len(privileges), 16)
        self.assertEqual(privileges["subscription_plans"], ("SELECT",))
        self.assertEqual(privileges["users"], ("SELECT", "INSERT", "UPDATE"))
        self.assertEqual(
            provision.RUNTIME_SCHEMA_READINESS_PRIVILEGES,
            {"alembic_version": ("SELECT",)},
        )
        self.assertFalse(
            {"DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}
            & {verb for verbs in privileges.values() for verb in verbs}
        )
        self.assertEqual(
            provision.CONTROL_PLANE_TABLES,
            (
                "release_observation_samples",
                "release_observation_runs",
                "data_migration_checkpoints",
                "data_migration_runs",
                "ops_feature_flag_audits",
                "ops_feature_flags",
                "acceptance_identity_bindings",
                "release_activations",
            ),
        )
        contract = json.loads(
            provision.DEFAULT_CONTRACT.read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["database_roles"]["runtime_schema_readiness_privileges"],
            {"alembic_version": ["SELECT"]},
        )

    def test_schema_readiness_grant_is_select_only_and_group_scoped(self) -> None:
        cursor = mock.MagicMock()
        cursor.fetchall.side_effect = (
            [{"tablename": "users"}],
            [{"tablename": "alembic_version"}],
            [{"rolname": "anon"}, {"rolname": "authenticated"}],
        )

        provision.configure_safe_baseline_database_roles(
            cursor,
            {"users": ("SELECT",)},
        )

        statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
        joined = "\n".join(statements)
        self.assertIn("Identifier('public', 'alembic_version')", joined)
        self.assertIn("GRANT", joined)
        self.assertIn("REVOKE ALL ON TABLE", joined)
        self.assertNotIn(
            "ENABLE ROW LEVEL SECURITY",
            "\n".join(
                statement
                for statement in statements
                if "alembic_version" in statement
            ),
        )

    def test_server_side_bootstrap_creates_scoped_inventory_and_migration_logins(self) -> None:
        source = BOOTSTRAP_SQL.read_text(encoding="utf-8")
        for required in (
            "vowpic_inventory_login",
            "default_transaction_read_only = on",
            "NOBYPASSRLS",
            "vowpic_migration_owner",
            "vowpic_migration_login",
            "vowpic_identity_owner",
            "vowpic_identity_service",
            "GRANT vowpic_identity_owner TO vowpic_migration_owner",
            "WITH INHERIT FALSE, SET TRUE",
            "vowpic_app_runtime",
            "vowpic_control_writer_login",
            "NOCREATEROLE",
            "NOBYPASSRLS",
            "SET role TO 'vowpic_migration_owner'",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public",
            "SET LOCAL ROLE vowpic_migration_owner",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public",
            "RESET ROLE",
            "class.relowner <> 'vowpic_migration_owner'::regrole",
            "procedure.proowner <> 'vowpic_migration_owner'::regrole",
            "procedure.proname = 'vowpic_runtime_statement_audit'",
            "procedure.proname = 'vowpic_rotate_application_database_logins'",
            "'runtime_password text, writer_password text'",
            "vowpic.database-bootstrap.secrets.v1",
            "vowpic_rotate_application_database_logins",
            "application database login rotation requires the migration login",
            "an existing VowPic login violates the least-privilege contract",
            "an application database login violates the least-privilege contract",
            "an existing VowPic NOLOGIN role has unexpected memberships",
            "a VowPic application group owns database objects",
        ):
            self.assertIn(required, source)
        self.assertNotIn("pg_read_all_data", source)
        self.assertNotIn("GRANT postgres", source)
        self.assertNotIn(
            "ALTER DEFAULT PRIVILEGES FOR ROLE vowpic_migration_owner",
            source,
        )
        self.assertNotIn("PASSWORD 'change", source)
        self.assertIsNone(re.search(r"(?<!NO)BYPASSRLS", source))
        self.assertIsNone(
            re.search(
                r"ALTER ROLE vowpic_(?:inventory_login|migration_login|app_runtime|"
                r"control_writer_login) WITH LOGIN NOSUPERUSER",
                source,
            )
        )
        self.assertIsNone(
            re.search(
                r"ALTER ROLE vowpic_(?:inventory_login|migration_login|app_runtime|"
                r"control_writer_login)[^\n]*NOBYPASSRLS",
                source,
            )
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION public.vowpic_rotate_application_database_logins(text, text)",
            source,
        )
        owner_self_grant = "set_config('createrole_self_grant', 'set', true)"
        owner_set_check = (
            "pg_has_role(CURRENT_USER, 'vowpic_migration_owner', 'SET')"
        )
        self.assertIn(owner_self_grant, source)
        self.assertIn(owner_set_check, source)
        self.assertNotIn("GRANT vowpic_migration_owner TO CURRENT_USER", source)
        self.assertLess(
            source.index(owner_self_grant),
            source.index("CREATE ROLE vowpic_migration_owner"),
        )
        self.assertLess(
            source.index(owner_set_check),
            source.index("'ALTER %s %I.%I OWNER TO vowpic_migration_owner'"),
        )
        default_role_start = source.index("SET LOCAL ROLE vowpic_migration_owner")
        default_table_grant = source.index(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public\n"
            "      GRANT SELECT ON TABLES TO vowpic_inventory_login;"
        )
        default_sequence_grant = source.index(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public\n"
            "      GRANT SELECT ON SEQUENCES TO vowpic_inventory_login;"
        )
        default_role_reset = source.index("RESET ROLE;", default_role_start)
        self.assertLess(default_role_start, default_table_grant)
        self.assertLess(default_table_grant, default_sequence_grant)
        self.assertLess(default_sequence_grant, default_role_reset)
        self.assertLess(
            source.index("class.relowner <> 'vowpic_migration_owner'::regrole"),
            source.index("'ALTER %s %I.%I OWNER TO vowpic_migration_owner'"),
        )
        self.assertLess(
            source.index("procedure.proowner <> 'vowpic_migration_owner'::regrole"),
            source.index(
                "'ALTER ROUTINE %I.%I(%s) OWNER TO vowpic_migration_owner'"
            ),
        )
        routine_transfer = source.index(
            "'ALTER ROUTINE %I.%I(%s) OWNER TO vowpic_migration_owner'"
        )
        self.assertLess(
            source.index("procedure.proname = 'vowpic_runtime_statement_audit'"),
            routine_transfer,
        )
        self.assertLess(
            source.index(
                "procedure.proname = 'vowpic_rotate_application_database_logins'"
            ),
            routine_transfer,
        )

    def test_pooler_url_keeps_project_suffix_and_replaces_password(self) -> None:
        result = provision.database_url_for_login(
            "postgresql://migration.ucqdgdjituqzzsnfprqd:old@"
            "aws-1-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require",
            "vowpic_app_runtime",
            "new:/ password",
        )
        parsed = urlsplit(result)
        self.assertEqual(
            unquote(parsed.username or ""),
            "vowpic_app_runtime.ucqdgdjituqzzsnfprqd",
        )
        self.assertEqual(unquote(parsed.password or ""), "new:/ password")
        self.assertEqual(parsed.hostname, "aws-1-us-east-1.pooler.supabase.com")
        self.assertEqual(parsed.query, "sslmode=require")
        self.assertNotIn("old", result)

    def test_direct_url_uses_the_exact_login_without_pooler_suffix(self) -> None:
        result = provision.database_url_for_login(
            "postgresql+asyncpg://postgres:old@db.example.com/vowpic?sslmode=require",
            "vowpic_control_writer_login",
            "writer-secret",
        )
        parsed = urlsplit(result)
        self.assertEqual(parsed.scheme, "postgresql")
        self.assertEqual(unquote(parsed.username or ""), "vowpic_control_writer_login")
        self.assertEqual(unquote(parsed.password or ""), "writer-secret")

    def test_pooler_password_propagation_retries_only_bounded_auth_failures(self) -> None:
        self.assertEqual(
            provision.POOLER_AUTH_RETRY_DELAYS_SECONDS,
            (0, 15, 30, 60),
        )
        runtime_url = (
            "postgresql://runtime.project:secret@"
            "aws-1-us-east-1.pooler.supabase.com:5432/postgres"
        )
        writer_url = (
            "postgresql://writer.project:secret@"
            "aws-1-us-east-1.pooler.supabase.com:5432/postgres"
        )
        auth_failure = provision.psycopg2.OperationalError(
            'password authentication failed for user "vowpic_app_runtime"'
        )
        expected = {"vowpic_app_runtime": {"current_user": "vowpic_app_runtime"}}
        with (
            mock.patch.object(
                provision,
                "prove_database_logins",
                side_effect=(auth_failure, auth_failure, expected),
            ) as prove,
            mock.patch.object(provision.time, "sleep") as sleep,
        ):
            result = provision.prove_database_logins_after_pooler_propagation(
                runtime_url,
                writer_url,
                {"users": ("SELECT",)},
            )
        self.assertEqual(result, expected)
        self.assertEqual(prove.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(15), mock.call(30)])

    def test_pooler_proof_does_not_retry_non_authentication_failures(self) -> None:
        role_url = (
            "postgresql://runtime.project:secret@"
            "aws-1-us-east-1.pooler.supabase.com:5432/postgres"
        )
        with (
            mock.patch.object(
                provision,
                "prove_database_logins",
                side_effect=provision.psycopg2.OperationalError("connection timed out"),
            ) as prove,
            mock.patch.object(provision.time, "sleep") as sleep,
        ):
            with self.assertRaises(provision.psycopg2.OperationalError):
                provision.prove_database_logins_after_pooler_propagation(
                    role_url,
                    role_url,
                    {"users": ("SELECT",)},
                )
        prove.assert_called_once()
        sleep.assert_not_called()

    def test_direct_database_authentication_failure_is_not_retried(self) -> None:
        direct_url = "postgresql://runtime:secret@db.example.com:5432/postgres"
        failure = provision.psycopg2.OperationalError(
            'password authentication failed for user "runtime"'
        )
        with (
            mock.patch.object(
                provision,
                "prove_database_logins",
                side_effect=failure,
            ) as prove,
            mock.patch.object(provision.time, "sleep") as sleep,
        ):
            with self.assertRaises(provision.psycopg2.OperationalError):
                provision.prove_database_logins_after_pooler_propagation(
                    direct_url,
                    direct_url,
                    {"users": ("SELECT",)},
                )
        prove.assert_called_once()
        sleep.assert_not_called()


class ProductionDatabaseLoginVercelTest(unittest.TestCase):
    def test_urls_are_sent_only_over_stdin_and_read_back_as_sensitive(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(args, *, input, text, capture_output, check):
            self.assertTrue(text)
            self.assertTrue(capture_output)
            self.assertFalse(check)
            calls.append((list(args), input))
            if args[1:3] == ["env", "list"]:
                stdout = json.dumps(
                    [
                        {"key": "DATABASE_URL", "type": "sensitive", "target": ["production"]},
                        {
                            "key": "CONTROL_PLANE_DATABASE_URL",
                            "type": "sensitive",
                            "target": ["production"],
                        },
                        {
                            "key": "CLEANUP_CRON_TOKEN",
                            "type": "sensitive",
                            "target": ["production"],
                        },
                    ]
                )
            else:
                stdout = ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "vercel"
            cli.touch()
            with mock.patch.object(provision.vercel_env.subprocess, "run", side_effect=fake_run):
                result = provision.vercel_env.publish_vercel_database_urls(
                    vercel_cli=str(cli),
                    token="vercel-token",
                    project_id="prj_Example123",
                    team_id="team_Example123",
                    runtime_url="postgresql://runtime:secret@db.example/vowpic",
                    writer_url="postgresql://writer:secret@db.example/vowpic",
                    cleanup_cron_token="cleanup-secret",
                )

        self.assertEqual(
            set(result),
            {"DATABASE_URL", "CONTROL_PLANE_DATABASE_URL", "CLEANUP_CRON_TOKEN"},
        )
        add_calls = [call for call in calls if call[0][1:3] == ["env", "add"]]
        self.assertEqual(len(add_calls), 3)
        self.assertEqual(add_calls[0][1], "postgresql://writer:secret@db.example/vowpic\n")
        self.assertEqual(add_calls[1][1], "postgresql://runtime:secret@db.example/vowpic\n")
        self.assertEqual(add_calls[2][1], "cleanup-secret\n")
        for args, _ in calls:
            command = " ".join(args)
            self.assertNotIn("postgresql://writer", command)
            self.assertNotIn("postgresql://runtime", command)
        for args, _ in add_calls:
            self.assertIn("--force", args)
            self.assertIn("--sensitive", args)

    def test_readback_rejects_a_non_sensitive_database_variable(self) -> None:
        def fake_run(args, *, input, text, capture_output, check):
            if args[1:3] == ["env", "list"]:
                stdout = json.dumps(
                    [
                        {"key": "DATABASE_URL", "type": "encrypted", "target": ["production"]},
                        {
                            "key": "CONTROL_PLANE_DATABASE_URL",
                            "type": "sensitive",
                            "target": ["production"],
                        },
                        {
                            "key": "CLEANUP_CRON_TOKEN",
                            "type": "sensitive",
                            "target": ["production"],
                        },
                    ]
                )
            else:
                stdout = ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "vercel"
            cli.touch()
            with mock.patch.object(provision.vercel_env.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(ValueError, "not a Production Sensitive"):
                    provision.vercel_env.publish_vercel_database_urls(
                        vercel_cli=str(cli),
                        token="vercel-token",
                        project_id="prj_Example123",
                        team_id="team_Example123",
                        runtime_url="postgresql://runtime:secret@db.example/vowpic",
                        writer_url="postgresql://writer:secret@db.example/vowpic",
                        cleanup_cron_token="cleanup-secret",
                    )


if __name__ == "__main__":
    unittest.main()
