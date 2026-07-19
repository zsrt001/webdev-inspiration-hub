"""COMMERCIAL_7A runtime database role contract and proof tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "verify_commercial_database_roles.py"
APPLY_SCRIPT = ROOT / "scripts" / "release" / "apply_additive_migrations.py"
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260710_0020_partner_consent.py"
)
SPEC = importlib.util.spec_from_file_location(
    "verify_commercial_database_roles_test",
    SCRIPT,
)
assert SPEC and SPEC.loader
roles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(roles)


def _valid_login_facts() -> dict[str, object]:
    return {
        "runtime_login": roles.RUNTIME_LOGIN,
        "runtime_login_can_login": True,
        "runtime_login_inherits": True,
        "runtime_login_superuser": False,
        "runtime_login_create_db": False,
        "runtime_login_create_role": False,
        "runtime_login_replication": False,
        "runtime_login_bypass_rls": False,
        "runtime_group_member": True,
        "control_writer_group_member": False,
    }


def _valid_table_facts(
    privileges: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, object]]:
    facts: dict[str, dict[str, object]] = {}
    for table, verbs in privileges.items():
        row: dict[str, object] = {
            "row_security_enabled": True,
            "force_row_security": table in {
                "media_assets",
                "generation_jobs",
                "partner_invites",
            },
            "public_privileges": [],
            "runtime_policy_names": [
                f"{table}_vowpic_runtime_{verb.lower()}" for verb in verbs
            ],
            "runtime_policy_commands": list(verbs),
        }
        for privilege in roles.CHECKED_TABLE_PRIVILEGES:
            row[f"runtime_{privilege.lower()}"] = privilege in verbs
            row[f"writer_{privilege.lower()}"] = False
        facts[table] = row
    return facts


class CommercialDatabaseRoleContractTest(unittest.TestCase):
    def test_contract_exactly_matches_the_unreleased_0020_migration(self) -> None:
        privileges, digest = roles.verify_contract_matches_migration()
        self.assertEqual(len(privileges), 31)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            privileges["subscription_checkout_intents"],
            ("SELECT", "INSERT", "UPDATE"),
        )
        self.assertEqual(
            privileges["payment_capture_facts"],
            ("SELECT", "INSERT"),
        )
        self.assertEqual(privileges["billing_catalog_versions"], ("SELECT",))
        self.assertNotIn("billing_catalog_import_audits", privileges)
        self.assertFalse(
            roles.FORBIDDEN_RUNTIME_PRIVILEGES
            & {verb for verbs in privileges.values() for verb in verbs}
        )

    def test_exact_runtime_surface_passes_and_is_sanitized(self) -> None:
        privileges, _ = roles.load_commercial_database_role_contract()
        proof = roles.validate_commercial_database_role_facts(
            privileges=privileges,
            table_facts=_valid_table_facts(privileges),
            login_facts=_valid_login_facts(),
        )
        self.assertTrue(proof["passed"])
        self.assertEqual(proof["table_count"], 31)
        self.assertNotIn("tables", proof)

    def test_destructive_runtime_or_writer_privilege_fails_closed(self) -> None:
        privileges, _ = roles.load_commercial_database_role_contract()
        for key, pattern in (
            ("runtime_delete", "runtime privileges"),
            ("writer_select", "control writer"),
        ):
            with self.subTest(key=key):
                facts = _valid_table_facts(privileges)
                facts["subscription_checkout_intents"][key] = True
                with self.assertRaisesRegex(ValueError, pattern):
                    roles.validate_commercial_database_role_facts(
                        privileges=privileges,
                        table_facts=facts,
                        login_facts=_valid_login_facts(),
                    )

    def test_missing_rls_or_policy_fails_closed(self) -> None:
        privileges, _ = roles.load_commercial_database_role_contract()
        facts = _valid_table_facts(privileges)
        facts["generation_jobs"]["row_security_enabled"] = False
        with self.assertRaisesRegex(ValueError, "row security"):
            roles.validate_commercial_database_role_facts(
                privileges=privileges,
                table_facts=facts,
                login_facts=_valid_login_facts(),
            )
        facts = _valid_table_facts(privileges)
        facts["generation_jobs"]["runtime_policy_names"] = []
        with self.assertRaisesRegex(ValueError, "runtime policies"):
            roles.validate_commercial_database_role_facts(
                privileges=privileges,
                table_facts=facts,
                login_facts=_valid_login_facts(),
            )

    def test_migration_applies_surface_and_entrypoint_proves_before_completion(
        self,
    ) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("def _configure_commercial_7a_runtime_surface()", migration)
        self.assertIn("_configure_commercial_7a_runtime_surface()", migration)
        self.assertIn("REVOKE ALL ON TABLE public.{table} FROM vowpic_runtime", migration)
        self.assertIn("TO vowpic_runtime", migration)
        source = APPLY_SCRIPT.read_text(encoding="utf-8")
        proof = source.index(
            "database_role_proof = await prove_commercial_database_roles(db)"
        )
        complete = source.index("await service.complete", proof)
        self.assertLess(proof, complete)


if __name__ == "__main__":
    unittest.main()
