"""Preview Supabase physical isolation and credential boundary tests."""

from __future__ import annotations

import unittest

from backend.scripts.verify_preview_database_isolation import (
    EXPECTED_CURRENT_USERS,
    EXPECTED_SESSIONS,
    validate_connection_facts,
    validate_preview_database_urls,
)


PREVIEW_REF = "abcdefghijklmnopqrst"
PRODUCTION_REF = "zyxwvutsrqponmlkjihg"


def _pooler_url(login: str, project_ref: str = PREVIEW_REF) -> str:
    return (
        f"postgresql://{login}.{project_ref}:secret@"
        "aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
    )


def _urls(project_ref: str = PREVIEW_REF) -> dict[str, str]:
    return {
        kind: _pooler_url(login, project_ref)
        for kind, login in EXPECTED_SESSIONS.items()
    }


class PreviewDatabaseIsolationTest(unittest.TestCase):
    def test_runtime_and_writer_use_the_provisioned_base_logins(self) -> None:
        self.assertEqual(EXPECTED_SESSIONS["runtime"], "vowpic_app_runtime")
        self.assertEqual(
            EXPECTED_SESSIONS["control_writer"],
            "vowpic_control_writer_login",
        )
        self.assertEqual(
            EXPECTED_SESSIONS["runtime"],
            EXPECTED_CURRENT_USERS["runtime"],
        )
        self.assertEqual(
            EXPECTED_SESSIONS["control_writer"],
            EXPECTED_CURRENT_USERS["control_writer"],
        )

    def test_urls_bind_distinct_scoped_logins_to_preview_not_production(self) -> None:
        proof = validate_preview_database_urls(
            _urls(),
            expected_preview_project_ref=PREVIEW_REF,
            production_supabase_url=f"https://{PRODUCTION_REF}.supabase.co",
        )
        self.assertEqual(proof["database"], "postgres")
        self.assertEqual(set(proof["credentials"]), set(EXPECTED_SESSIONS))
        self.assertNotEqual(
            proof["preview_project_ref_sha256"],
            proof["production_project_ref_sha256"],
        )

    def test_rejects_same_physical_project_as_production(self) -> None:
        with self.assertRaisesRegex(ValueError, "different Supabase projects"):
            validate_preview_database_urls(
                _urls(),
                expected_preview_project_ref=PREVIEW_REF,
                production_supabase_url=f"https://{PREVIEW_REF}.supabase.co",
            )

    def test_rejects_one_credential_from_another_project(self) -> None:
        urls = _urls()
        urls["runtime"] = _pooler_url(
            EXPECTED_SESSIONS["runtime"],
            PRODUCTION_REF,
        )
        with self.assertRaisesRegex(ValueError, "declared project"):
            validate_preview_database_urls(
                urls,
                expected_preview_project_ref=PREVIEW_REF,
                production_supabase_url=f"https://{PRODUCTION_REF}.supabase.co",
            )

    def test_connection_facts_require_one_server_and_exact_roles(self) -> None:
        facts = {
            kind: {
                "session_user": EXPECTED_SESSIONS[kind],
                "current_user": EXPECTED_CURRENT_USERS[kind],
                "database": "postgres",
                "default_read_only": "on" if kind == "control_reader" else "off",
                "session_can_login": True,
                "session_superuser": False,
                "session_create_db": False,
                "session_create_role": False,
                "session_replication": False,
                "session_bypass_rls": False,
                "server_address": "10.0.0.9",
            }
            for kind in EXPECTED_SESSIONS
        }
        proof = validate_connection_facts(facts, expected_database="postgres")
        self.assertEqual(set(proof["sessions"]), set(EXPECTED_SESSIONS))
        facts["runtime"]["session_superuser"] = True
        with self.assertRaisesRegex(ValueError, "least privilege"):
            validate_connection_facts(facts, expected_database="postgres")
