from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "retire_obsolete_reader_logins_in_vercel_build.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-obsolete-reader-login-cleanup.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("obsolete_reader_cleanup", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("obsolete reader cleanup module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup = _load_module()


def _facts(*, memberships: list[str]) -> dict[str, object]:
    return {
        "rolcanlogin": True,
        "rolinherit": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "memberships": memberships,
        "owns_objects": False,
    }


class _Cursor:
    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[object, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self):
        return self.rows.pop(0)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_instance = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self.cursor_instance


class ObsoleteReaderLoginCleanupTests(unittest.TestCase):
    def test_accepts_only_the_exact_least_privilege_outer_role(self) -> None:
        cleanup._assert_role_facts(
            _facts(memberships=[cleanup.INVENTORY_LOGIN]),
            login=cleanup.OBSOLETE_LOGINS[0],
            expected_memberships={cleanup.INVENTORY_LOGIN},
        )

    def test_rejects_elevated_owned_or_differently_scoped_roles(self) -> None:
        for field, value in (
            ("rolsuper", True),
            ("rolcreatedb", True),
            ("rolcreaterole", True),
            ("rolreplication", True),
            ("rolbypassrls", True),
            ("owns_objects", True),
            ("rolcanlogin", False),
            ("rolinherit", False),
            ("memberships", ["vowpic_control_writer"]),
        ):
            facts = _facts(memberships=[cleanup.INVENTORY_LOGIN])
            facts[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "cleanup contract"):
                    cleanup._assert_role_facts(
                        facts,
                        login=cleanup.OBSOLETE_LOGINS[0],
                        expected_memberships={cleanup.INVENTORY_LOGIN},
                    )

    def test_extracts_pooler_and_direct_project_targets(self) -> None:
        self.assertEqual(
            cleanup._database_target(
                "postgresql://postgres.project:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
            ("project", "postgres"),
        )
        self.assertEqual(
            cleanup._database_target(
                "postgresql://postgres:secret@"
                "db.project.supabase.co:5432/postgres?sslmode=require"
            ),
            ("project", "postgres"),
        )

    def test_rejects_non_supabase_or_non_tls_authorities(self) -> None:
        for url in (
            "postgresql://postgres.project:secret@example.com:5432/postgres?sslmode=require",
            "postgresql://postgres.project:secret@aws.pooler.supabase.com:6543/postgres",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    cleanup._database_target(url)

    def test_retires_both_exact_roles_after_full_prevalidation(self) -> None:
        safe_inventory = _facts(memberships=[])
        safe_outer = _facts(memberships=[cleanup.INVENTORY_LOGIN])
        cursor = _Cursor(
            [
                {
                    "session_user": "postgres",
                    "current_user": "postgres",
                    "rolsuper": True,
                },
                safe_inventory,
                safe_outer,
                safe_outer,
                None,
                None,
                safe_inventory,
            ]
        )
        with mock.patch.object(
            cleanup.psycopg2,
            "connect",
            return_value=_Connection(cursor),
        ):
            result = cleanup.retire_obsolete_reader_logins(
                "postgresql://postgres.project:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
                "project",
            )
        self.assertEqual(
            result["obsolete_logins"],
            {login: "DELETED" for login in cleanup.OBSOLETE_LOGINS},
        )
        drops = [
            repr(statement)
            for statement, _parameters in cursor.calls
            if "DROP ROLE" in repr(statement)
        ]
        self.assertEqual(len(drops), 2)
        for login in cleanup.OBSOLETE_LOGINS:
            self.assertTrue(any(login in statement for statement in drops))

    def test_rejects_an_unsafe_second_role_before_any_role_ddl(self) -> None:
        unsafe_outer = _facts(memberships=[cleanup.INVENTORY_LOGIN])
        unsafe_outer["owns_objects"] = True
        cursor = _Cursor(
            [
                {
                    "session_user": "postgres",
                    "current_user": "postgres",
                    "rolsuper": True,
                },
                _facts(memberships=[]),
                _facts(memberships=[cleanup.INVENTORY_LOGIN]),
                unsafe_outer,
            ]
        )
        with mock.patch.object(
            cleanup.psycopg2,
            "connect",
            return_value=_Connection(cursor),
        ):
            with self.assertRaisesRegex(ValueError, "cleanup contract"):
                cleanup.retire_obsolete_reader_logins(
                    "postgresql://postgres.project:secret@"
                    "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
                    "project",
                )
        self.assertFalse(
            any(not isinstance(statement, str) for statement, _parameters in cursor.calls)
        )

    def test_workflow_is_protected_exact_and_self_cleaning(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        self.assertIn("environment: production", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("PRODUCTION_READ_ONLY_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_CONTROL_READ_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn(
            '--build-env "PRODUCTION_READ_ONLY_DATABASE_URL=',
            workflow,
        )
        self.assertNotIn("VERCEL_AUTOMATION_BYPASS_HEADER", workflow)
        self.assertIn("./.github/actions/setup-release-python", workflow)
        self.assertIn('pull --yes --environment=production', workflow)
        self.assertIn('--vercel-env-file "$ENV_FILE"', workflow)
        self.assertIn('test ! -e "$GITHUB_WORKSPACE/.vercel"', workflow)
        self.assertIn('test ! -L "$ENV_FILE"', workflow)
        self.assertIn('chmod 600 "$ENV_FILE"', workflow)
        self.assertIn('trap cleanup_sensitive EXIT', workflow)
        self.assertIn('rm -rf -- "$GITHUB_WORKSPACE/.vercel"', workflow)
        self.assertNotIn("vercel deploy", workflow)
        self.assertNotIn("--skip-domain", workflow)
        self.assertNotIn("deployment-cleanup.json", workflow)
        self.assertIn("production-obsolete-reader-login-cleanup", workflow)
        self.assertNotIn("DROP OWNED", workflow)
        self.assertNotIn("REASSIGN OWNED", workflow)

    def test_script_never_uses_broad_role_cleanup(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('sql.SQL("DROP ROLE {}")', source)
        self.assertNotIn("DROP OWNED", source)
        self.assertNotIn("REASSIGN OWNED", source)
        self.assertEqual(
            set(cleanup.OBSOLETE_LOGINS),
            {
                "vowpic_release_control_read_login",
                "vowpic_release_inventory_login",
            },
        )

    def test_direct_main_replaces_only_the_sanitized_proof(self) -> None:
        proof = {
            "schema": cleanup.SCHEMA,
            "state": "PASSED",
            "database": "postgres",
            "inventory_login": "PRESERVED",
            "obsolete_logins": {
                login: "ALREADY_ABSENT" for login in cleanup.OBSOLETE_LOGINS
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env.production.local"
            env_file.write_text(
                'DATABASE_URL="protected"\nOTHER_SECRET="not-read"\n',
                encoding="utf-8",
            )
            os.chmod(env_file, 0o600)
            output_path = root / "proof.json"
            output_path.write_text('{"state":"FAILED"}\n', encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    cleanup,
                    "retire_obsolete_reader_logins",
                    return_value=proof,
                ) as retire,
                mock.patch.object(
                    cleanup.sys,
                    "argv",
                    [
                        "cleanup",
                        "--vercel-env-file",
                        str(env_file),
                        "--expected-project-ref",
                        "project",
                        "--output",
                        str(output_path),
                    ],
                ),
                redirect_stdout(stdout),
            ):
                self.assertEqual(cleanup.main(), 0)
            retire.assert_called_once_with("protected", "project")
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), proof)
            self.assertEqual(
                json.loads(stdout.getvalue()),
                {"schema": cleanup.SCHEMA, "state": "PASSED"},
            )
            self.assertFalse(output_path.with_name(".proof.json.tmp").exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode contract is enforced in CI")
    def test_env_reader_rejects_group_readable_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.production.local"
            env_file.write_text('DATABASE_URL="protected"\n', encoding="utf-8")
            os.chmod(env_file, 0o640)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                cleanup._load_admin_url(env_file)


if __name__ == "__main__":
    unittest.main()
