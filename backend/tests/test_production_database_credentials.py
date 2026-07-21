from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_production_database_credentials.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-database-credential-proof.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "verify_production_database_credentials",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("credential proof module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load_module()


def _facts(kind: str) -> dict[str, object]:
    session = proof.EXPECTED_SESSIONS[kind]
    current = proof.EXPECTED_CURRENT_USERS[kind]
    session_memberships: dict[str, list[str]] = {
        "runtime": [current],
        "control_writer": [current],
        "control_reader": [],
    }
    current_memberships: dict[str, list[str]] = {
        "runtime": ["vowpic_identity_service", "vowpic_runtime"],
        "control_writer": ["vowpic_control_writer"],
        "control_reader": [],
    }
    return {
        "session_user": session,
        "current_user": current,
        "database": "postgres",
        "default_read_only": "on" if kind == "control_reader" else "off",
        "session_can_login": True,
        "session_inherits": True,
        "session_superuser": False,
        "session_create_db": False,
        "session_create_role": False,
        "session_replication": False,
        "session_bypass_rls": False,
        "session_direct_memberships": session_memberships[kind],
        "current_direct_memberships": current_memberships[kind],
        "runtime_member": kind == "runtime",
        "control_writer_member": kind == "control_writer",
        "schema_select": True,
        "schema_update": False,
        "flags_select": True,
        "flags_update": kind == "control_writer",
        "activations_select": True,
        "activations_insert": kind == "control_writer",
        "activations_update": kind == "control_writer",
        "activations_delete": kind == "control_writer",
        "users_select": kind in {"runtime", "control_reader"},
        "users_update": False,
    }


class ProductionDatabaseCredentialProofTests(unittest.TestCase):
    def test_validates_three_distinct_tls_pooler_urls(self) -> None:
        urls = {
            kind: (
                "postgresql://"
                f"{login}.ucqdgdjituqzzsnfprqd:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            )
            for kind, login in proof.EXPECTED_SESSIONS.items()
        }
        parsed = proof.validate_database_urls(urls)
        self.assertEqual(set(parsed), set(proof.EXPECTED_SESSIONS))
        self.assertEqual(parsed["control_reader"]["login"], "vowpic_inventory_login")

    def test_accepts_supported_pooler_endpoints_for_one_project(self) -> None:
        urls = {
            "runtime": (
                "postgresql://vowpic_release_runtime_login.project:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
            "control_writer": (
                "postgresql://vowpic_release_control_login.project:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
            "control_reader": (
                "postgresql://vowpic_inventory_login.project:secret@"
                "aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
            ),
        }
        parsed = proof.validate_database_urls(urls)
        self.assertEqual(parsed["control_reader"]["pooler_port"], 5432)

    def test_rejects_credentials_from_different_supabase_projects(self) -> None:
        urls = {
            kind: (
                "postgresql://"
                f"{login}.project:secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            )
            for kind, login in proof.EXPECTED_SESSIONS.items()
        }
        urls["control_reader"] = urls["control_reader"].replace(
            ".project:secret@", ".other-project:secret@"
        )
        with self.assertRaisesRegex(ValueError, "one Supabase project"):
            proof.validate_database_urls(urls)

    def test_rejects_reused_login(self) -> None:
        shared = (
            "postgresql://vowpic_release_runtime_login.ucqdgdjituqzzsnfprqd:secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        with self.assertRaisesRegex(ValueError, "invalid login"):
            proof.validate_database_urls(
                {kind: shared for kind in proof.EXPECTED_SESSIONS}
            )

    def test_accepts_the_least_privilege_inventory_login(self) -> None:
        result = proof.validate_database_facts(
            {kind: _facts(kind) for kind in proof.EXPECTED_SESSIONS}
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["schema"], proof.SCHEMA)
        self.assertEqual(
            result["credentials"]["control_reader"]["session_user"],
            "vowpic_inventory_login",
        )

    def test_rejects_inventory_login_role_membership(self) -> None:
        facts = {kind: _facts(kind) for kind in proof.EXPECTED_SESSIONS}
        facts["control_reader"]["session_direct_memberships"] = [
            "vowpic_control_writer"
        ]
        with self.assertRaisesRegex(ValueError, "control-reader"):
            proof.validate_database_facts(facts)

    def test_rejects_control_reader_write_privilege(self) -> None:
        facts = {kind: _facts(kind) for kind in proof.EXPECTED_SESSIONS}
        facts["control_reader"]["activations_update"] = True
        with self.assertRaisesRegex(ValueError, "control-reader"):
            proof.validate_database_facts(facts)

    def test_workflow_uses_only_protected_environment_secrets(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("environment: production", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        for name in proof.ENVIRONMENTS.values():
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", workflow)
        self.assertNotIn("PRODUCTION_CONTROL_READ_DATABASE_URL", workflow)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_connection_proof_does_not_use_postgresql_keyword_as_alias(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("JOIN pg_roles active_role", source)
        self.assertNotIn("JOIN pg_roles current_role", source)

    def test_connection_query_executes_on_postgresql_when_available(self) -> None:
        database_url = os.environ.get(
            "PRODUCTION_DATABASE_PROOF_TEST_URL", ""
        ).strip()
        if not database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
            self.skipTest("PostgreSQL proof test URL is unavailable")
        facts = proof._connection_facts(database_url)
        self.assertTrue(facts["database"])
        self.assertTrue(facts["session_user"])
        self.assertTrue(facts["current_user"])

    def test_ci_binds_proof_query_to_the_running_postgresql_service(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "PRODUCTION_DATABASE_PROOF_TEST_URL: "
            "postgresql://postgres:postgres@127.0.0.1:5432/vowpic_rls_test",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
