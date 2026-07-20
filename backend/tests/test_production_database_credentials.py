from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

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
        self.assertNotIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("credential-url.txt", workflow)


if __name__ == "__main__":
    unittest.main()
