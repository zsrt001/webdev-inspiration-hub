from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_production_database_credentials.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-database-credential-proof.yml"
REPAIR_SCRIPT = (
    ROOT / "scripts" / "release" / "repair_production_control_reader_credential.py"
)
REPAIR_WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "production-control-reader-credential-repair.yml"
)
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


def _load_repair_module():
    release_scripts = str(REPAIR_SCRIPT.parent)
    if release_scripts not in sys.path:
        sys.path.insert(0, release_scripts)
    spec = importlib.util.spec_from_file_location(
        "repair_production_control_reader_credential",
        REPAIR_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("credential repair module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair = _load_repair_module()


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[object, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def _facts(kind: str) -> dict[str, object]:
    session = proof.EXPECTED_SESSIONS[kind]
    current = proof.EXPECTED_CURRENT_USERS[kind]
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
        "session_direct_memberships": [current],
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

    def test_rejects_reused_login(self) -> None:
        shared = (
            "postgresql://vowpic_release_runtime_login.ucqdgdjituqzzsnfprqd:secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        with self.assertRaisesRegex(ValueError, "invalid login"):
            proof.validate_database_urls({kind: shared for kind in proof.EXPECTED_SESSIONS})

    def test_accepts_least_privilege_facts(self) -> None:
        result = proof.validate_database_facts(
            {kind: _facts(kind) for kind in proof.EXPECTED_SESSIONS}
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["schema"], proof.SCHEMA)

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

    def test_builds_control_reader_url_from_two_proven_targets(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        result = repair.build_control_reader_url(runtime, writer, "new-secret")
        self.assertEqual(
            result,
            "postgresql://vowpic_release_control_read_login.project:new-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
        )
        self.assertNotIn("runtime-secret", result)
        self.assertNotIn("writer-secret", result)

    def test_rejects_control_reader_rotation_when_source_targets_differ(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.other:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        with self.assertRaisesRegex(ValueError, "one target"):
            repair.build_control_reader_url(runtime, writer, "new-secret")

    def test_recovers_control_reader_url_from_existing_password(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        result = repair.recover_control_reader_url(runtime, writer, "reader-secret")
        self.assertIn(
            "vowpic_release_control_read_login.project:reader-secret@",
            result,
        )

    def test_preserves_an_existing_valid_control_reader_url(self) -> None:
        urls = {
            "runtime": (
                "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
            "control_writer": (
                "postgresql://vowpic_release_control_login.project:writer-secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
            "control_reader": (
                "postgresql://vowpic_release_control_read_login.project:reader-secret@"
                "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
            ),
        }
        result = repair.recover_control_reader_url(
            urls["runtime"],
            urls["control_writer"],
            urls["control_reader"],
        )
        self.assertEqual(result, urls["control_reader"])

    def test_removes_copy_whitespace_before_strict_url_validation(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        copied = (
            "postgresql://vowpic_release_control_read_login.project:\n"
            "reader-secret@aws-1-us-east-1.pooler.supabase.com:6543/\n"
            "postgres?sslmode=require"
        )
        result = repair.recover_control_reader_url(runtime, writer, copied)
        self.assertEqual(
            result,
            "postgresql://vowpic_release_control_read_login.project:reader-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
        )

    def test_recovers_reader_candidate_from_exact_legacy_inventory_target(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        legacy = (
            "postgresql://vowpic_inventory_login.project:inventory-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        result = repair.recover_control_reader_url_from_legacy_inventory(
            runtime,
            writer,
            legacy,
        )
        self.assertEqual(
            result,
            "postgresql://vowpic_release_control_read_login.project:inventory-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
        )

    def test_rejects_legacy_inventory_url_from_another_target(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        legacy = (
            "postgresql://vowpic_inventory_login.other:inventory-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        with self.assertRaisesRegex(ValueError, "one target"):
            repair.recover_control_reader_url_from_legacy_inventory(
                runtime,
                writer,
                legacy,
            )

    def test_proves_legacy_candidate_only_after_existing_secret_fails(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        legacy = (
            "postgresql://vowpic_inventory_login.project:inventory-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        safe_proof = {"passed": True}
        with mock.patch.object(
            repair,
            "prove_production_database_credentials",
            side_effect=[
                repair.psycopg2.OperationalError("authentication failed"),
                safe_proof,
            ],
        ) as prove:
            reader_url, result = repair.recover_and_prove_control_reader(
                runtime,
                writer,
                "stale-reader-secret",
                legacy,
            )
        self.assertEqual(result, safe_proof)
        self.assertIn(":inventory-secret@", reader_url)
        self.assertEqual(prove.call_count, 2)
        self.assertIn(
            ":stale-reader-secret@",
            prove.call_args_list[0].args[0]["control_reader"],
        )
        self.assertIn(
            ":inventory-secret@",
            prove.call_args_list[1].args[0]["control_reader"],
        )

    def test_does_not_hide_programming_errors_while_trying_candidates(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        with mock.patch.object(
            repair,
            "prove_production_database_credentials",
            side_effect=RuntimeError("proof implementation failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "implementation failed"):
                repair.recover_and_prove_control_reader(
                    runtime,
                    writer,
                    "reader-secret",
                    "",
                )

    def test_fetches_only_the_decrypted_production_database_url(self) -> None:
        admin_url = (
            "postgresql://postgres.project:admin-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )

        def opener(request, *, timeout):
            self.assertEqual(timeout, 20)
            self.assertIn("decrypt=true", request.full_url)
            self.assertIn("teamId=team", request.full_url)
            self.assertEqual(request.get_header("Authorization"), "Bearer token")
            return _Response(
                {
                    "envs": [
                        {
                            "key": "DATABASE_URL",
                            "target": ["preview"],
                            "decrypted": True,
                            "value": "postgresql://wrong",
                        },
                        {
                            "key": "DATABASE_URL",
                            "target": ["production"],
                            "decrypted": True,
                            "value": admin_url,
                        },
                    ]
                }
            )

        self.assertEqual(
            repair.fetch_vercel_production_database_url(
                "token",
                "project",
                "team",
                opener=opener,
            ),
            admin_url,
        )

    def test_rejects_ambiguous_vercel_production_database_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "one decrypted"):
            repair.fetch_vercel_production_database_url(
                "token",
                "project",
                "team",
                opener=lambda *_args, **_kwargs: _Response(
                    {
                        "envs": [
                            {
                                "key": "DATABASE_URL",
                                "target": ["production"],
                                "decrypted": True,
                                "value": "postgresql://first",
                            },
                            {
                                "key": "DATABASE_URL",
                                "target": ["production"],
                                "decrypted": True,
                                "value": "postgresql://second",
                            },
                        ]
                    }
                ),
            )

    def test_rotates_only_after_protected_candidates_fail(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        admin = (
            "postgresql://postgres.project:admin-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        password = "r" * 64
        expected_proof = {"passed": True}
        with (
            mock.patch.object(
                repair,
                "recover_and_prove_control_reader",
                side_effect=ValueError("no candidate"),
            ),
            mock.patch.object(
                repair,
                "fetch_vercel_production_database_url",
                return_value=admin,
            ) as fetch,
            mock.patch.object(repair, "rotate_control_reader_password") as rotate,
            mock.patch.object(
                repair,
                "prove_control_reader_after_pooler_propagation",
                return_value=expected_proof,
            ) as prove,
        ):
            reader_url, result = repair.recover_prove_or_rotate_control_reader(
                runtime,
                writer,
                password,
                "",
                vercel_token="token",
                vercel_project_id="project",
                vercel_team_id="team",
            )
        self.assertEqual(result, expected_proof)
        self.assertIn(f":{password}@", reader_url)
        fetch.assert_called_once_with("token", "project", "team")
        rotate.assert_called_once_with(admin, runtime, writer, password)
        prove.assert_called_once_with(runtime, writer, reader_url)

    def test_admin_recovery_rotates_only_the_exact_least_privilege_login(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        admin = (
            "postgresql://postgres.project:admin-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        password = "r" * 64
        cursor = _Cursor(
            [
                {"session_user": "postgres", "current_user": "postgres", "rolsuper": True},
                {
                    "rolcanlogin": True,
                    "rolinherit": True,
                    "rolsuper": False,
                    "rolcreatedb": False,
                    "rolcreaterole": False,
                    "rolreplication": False,
                    "rolbypassrls": False,
                    "memberships": ["vowpic_inventory_login"],
                    "owns_objects": False,
                },
            ]
        )
        with mock.patch.object(
            repair.psycopg2,
            "connect",
            return_value=_Connection(cursor),
        ):
            repair.rotate_control_reader_password(admin, runtime, writer, password)
        self.assertEqual(len(cursor.calls), 3)
        self.assertEqual(cursor.calls[-1][1], (password,))
        self.assertNotIn(password, str(cursor.calls[-1][0]))

    def test_admin_recovery_rejects_a_non_postgres_authority_before_rotation(self) -> None:
        runtime = (
            "postgresql://vowpic_release_runtime_login.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        writer = (
            "postgresql://vowpic_release_control_login.project:writer-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        candidate = (
            "postgresql://vowpic_app_runtime.project:runtime-secret@"
            "aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        cursor = _Cursor(
            [
                {
                    "session_user": "vowpic_app_runtime",
                    "current_user": "vowpic_app_runtime",
                    "rolsuper": False,
                }
            ]
        )
        with mock.patch.object(
            repair.psycopg2,
            "connect",
            return_value=_Connection(cursor),
        ):
            with self.assertRaisesRegex(ValueError, "not a postgres"):
                repair.rotate_control_reader_password(
                    candidate,
                    runtime,
                    writer,
                    "r" * 64,
                )
        self.assertEqual(len(cursor.calls), 1)

    def test_failure_proof_is_sanitized_and_createable_without_an_encrypted_url(self) -> None:
        with self.subTest("controlled contract failure"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "proof.json"
                repair.write_failure_proof(path, ValueError("fixed safe reason"))
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(document["state"], "FAILED")
                self.assertEqual(document["reason"], "fixed safe reason")
                self.assertEqual(document["schema"], repair.FAILURE_SCHEMA)
        secret_dsn = "postgresql://postgres:must-not-leak@example.invalid/postgres"
        error = repair.psycopg2.OperationalError(secret_dsn)
        self.assertEqual(
            repair.sanitized_failure_reason(error),
            "database operation failed",
        )
        self.assertNotIn(secret_dsn, repair.sanitized_failure_reason(error))

    def test_encrypts_repaired_url_to_one_time_rsa_recipient(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        public_key = private_key.public_key().public_bytes(
            encoding=repair.serialization.Encoding.PEM,
            format=repair.serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        encrypted = repair.encrypt_secret("postgresql://protected", public_key)
        decrypted = private_key.decrypt(
            encrypted,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        self.assertEqual(decrypted, b"postgresql://protected")

    def test_repair_workflow_is_manual_protected_and_normalizes_old_secret(self) -> None:
        workflow = REPAIR_WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        self.assertIn("environment: production", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn(
            "PRODUCTION_CONTROL_READ_DATABASE_URL: "
            "${{ secrets.PRODUCTION_CONTROL_READ_DATABASE_URL }}",
            workflow,
        )
        self.assertIn(
            "PRODUCTION_READ_ONLY_DATABASE_URL: "
            "${{ secrets.PRODUCTION_READ_ONLY_DATABASE_URL }}",
            workflow,
        )
        self.assertIn(
            "--legacy-read-only-secret-env PRODUCTION_READ_ONLY_DATABASE_URL",
            workflow,
        )
        for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_ORG_ID"):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", workflow)
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("credential-url.txt", workflow)
        self.assertRegex(
            workflow,
            r"(?s)- name: Upload encrypted credential and sanitized proof\s+if: always\(\)",
        )


if __name__ == "__main__":
    unittest.main()
