from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "retire_obsolete_reader_logins_in_vercel_build.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-obsolete-reader-login-cleanup.yml"
CONFIG = ROOT / "vercel.obsolete-reader-cleanup.json"
CLEANUP_REQUIREMENTS = (
    ROOT / "scripts" / "release" / "obsolete_reader_cleanup_requirements.txt"
)
PRODUCTION_REQUIREMENTS = ROOT / "requirements.txt"


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
        self.assertIn("VERCEL_AUTOMATION_BYPASS_HEADER", workflow)
        self.assertIn('pull --yes --environment=production', workflow)
        self.assertIn('test ! -e "$GITHUB_WORKSPACE/.vercel"', workflow)
        self.assertIn('rm -f -- "$GITHUB_WORKSPACE/.vercel/.env.production.local"', workflow)
        self.assertIn('DEPLOY_ROOT="$STATE_DIR/deployment"', workflow)
        self.assertIn('install -m 700 -d "$DEPLOY_ROOT/api" "$DEPLOY_ROOT/.vercel"', workflow)
        self.assertIn('"$DEPLOY_ROOT/api/index.py"', workflow)
        self.assertIn('"$DEPLOY_ROOT/requirements.txt"', workflow)
        self.assertIn('"$DEPLOY_ROOT/vercel.json"', workflow)
        self.assertIn('"$DEPLOY_ROOT/.vercel/project.json"', workflow)
        self.assertIn('cd "$DEPLOY_ROOT"', workflow)
        self.assertIn('private cleanup deployment staging is not minimal', workflow)
        self.assertIn('private cleanup dependency lock is not hash-pinned', workflow)
        self.assertIn('--logs', workflow)
        self.assertIn('private cleanup deployment build failed', workflow)
        self.assertIn('private cleanup deployment build proof is invalid', workflow)
        self.assertNotIn('build.get("entrypoint")', workflow)
        self.assertIn('rm -rf -- "$GITHUB_WORKSPACE/.vercel" "$STATE_DIR/deployment"', workflow)
        self.assertIn('python -c \'import secrets; print(secrets.token_hex(32))\'', workflow)
        self.assertIn('echo "::add-mask::$TRIGGER_TOKEN"', workflow)
        self.assertIn('--env "CLEANUP_TRIGGER_TOKEN=$TRIGGER_TOKEN"', workflow)
        self.assertIn('--skip-domain', workflow)
        self.assertIn('--request POST', workflow)
        self.assertIn('Authorization: Bearer $TRIGGER_TOKEN', workflow)
        self.assertIn('x-vercel-protection-bypass: $VERCEL_AUTOMATION_BYPASS_HEADER', workflow)
        self.assertIn('"$VERCEL_CLI" remove "$DEPLOYMENT_ID" --yes', workflow)
        self.assertIn('test "$STATUS_CODE" = "404"', workflow)
        self.assertLess(
            workflow.index('joinpath("deployment-id.txt").write_text'),
            workflow.index('private cleanup deployment URL is invalid'),
        )
        self.assertIn("deployment-cleanup.json", workflow)
        self.assertIn("production-obsolete-reader-login-cleanup", workflow)
        self.assertNotIn("DROP OWNED", workflow)
        self.assertNotIn("REASSIGN OWNED", workflow)

    def test_cleanup_dependency_lock_is_the_exact_hashed_production_pin(self) -> None:
        production = PRODUCTION_REQUIREMENTS.read_text(encoding="utf-8")
        start = production.index("psycopg2-binary==2.9.12 \\\n")
        end = production.index("\n    # via", start)
        expected = production[start:end] + "\n"
        cleanup_requirements = CLEANUP_REQUIREMENTS.read_text(encoding="utf-8")
        self.assertEqual(cleanup_requirements, expected)
        self.assertEqual(cleanup_requirements.count("=="), 1)
        self.assertGreater(cleanup_requirements.count("--hash=sha256:"), 1)

    def test_function_deployment_contains_only_the_cleanup_entrypoint(self) -> None:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(
            config["builds"],
            [{"src": "api/index.py", "use": "@vercel/python@6.50.0"}],
        )
        self.assertEqual(
            config["routes"],
            [
                {
                    "src": "/api/obsolete-reader-cleanup",
                    "methods": ["POST"],
                    "dest": "/api/index.py",
                },
                {"src": "/(.*)", "status": 404},
            ],
        )

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

    def test_authenticated_function_returns_only_the_sanitized_proof(self) -> None:
        proof = {
            "schema": cleanup.SCHEMA,
            "state": "PASSED",
            "database": "postgres",
            "inventory_login": "PRESERVED",
            "obsolete_logins": {
                login: "ALREADY_ABSENT" for login in cleanup.OBSOLETE_LOGINS
            },
        }
        environment = {
            cleanup.TRIGGER_TOKEN_ENV: "a" * 64,
            cleanup.EXPECTED_PROJECT_REF_ENV: "a" * 20,
            cleanup.ADMIN_DATABASE_URL_ENV: "protected",
        }
        with mock.patch.object(
            cleanup,
            "retire_obsolete_reader_logins",
            return_value=proof,
        ) as retire:
            status, payload = cleanup.handle_cleanup_request(
                f"Bearer {environment[cleanup.TRIGGER_TOKEN_ENV]}",
                environment,
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload, proof)
        retire.assert_called_once_with("protected", "a" * 20)

    def test_unauthenticated_function_never_opens_the_database(self) -> None:
        environment = {
            cleanup.TRIGGER_TOKEN_ENV: "b" * 64,
            cleanup.EXPECTED_PROJECT_REF_ENV: "b" * 20,
            cleanup.ADMIN_DATABASE_URL_ENV: "protected",
        }
        with mock.patch.object(cleanup, "retire_obsolete_reader_logins") as retire:
            status, payload = cleanup.handle_cleanup_request("Bearer wrong", environment)
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"state": "NOT_FOUND"})
        retire.assert_not_called()

    def test_function_failure_is_sanitized(self) -> None:
        environment = {
            cleanup.TRIGGER_TOKEN_ENV: "c" * 64,
            cleanup.EXPECTED_PROJECT_REF_ENV: "c" * 20,
            cleanup.ADMIN_DATABASE_URL_ENV: "protected-secret-url",
        }
        with mock.patch.object(
            cleanup,
            "retire_obsolete_reader_logins",
            side_effect=cleanup.psycopg2.OperationalError("protected-secret-url"),
        ):
            status, payload = cleanup.handle_cleanup_request(
                f"Bearer {environment[cleanup.TRIGGER_TOKEN_ENV]}",
                environment,
            )
        self.assertEqual(status, 409)
        self.assertEqual(payload["state"], "FAILED")
        self.assertEqual(payload["reason"], "database cleanup failed")
        self.assertNotIn("protected-secret-url", json.dumps(payload))

    def test_function_rejects_invalid_trigger_contract(self) -> None:
        status, payload = cleanup.handle_cleanup_request(
            "Bearer short",
            {cleanup.TRIGGER_TOKEN_ENV: "short"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["state"], "FAILED")
        self.assertNotIn("short", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
