"""Partner consent migration and schema contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260710_0020_partner_consent.py"


class PartnerInviteMigrationTest(unittest.TestCase):
    def test_revision_chains_from_generation_jobs_and_defines_all_tables(self) -> None:
        self.assertTrue(MIGRATION.is_file())
        spec = importlib.util.spec_from_file_location("partner_consent_migration", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertEqual(module.revision, "20260710_0020")
        self.assertEqual(module.down_revision, "20260710_0019")
        source = MIGRATION.read_text(encoding="utf-8")
        for table in (
            "partner_invites",
            "partner_invite_events",
            "partner_consent_cases",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY", source)
        self.assertIn("ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY", source)

    def test_database_guards_lifecycle_token_and_one_time_bindings(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for required in (
            "ck_partner_invites_status",
            "ck_partner_invites_identity_distinct",
            "ck_partner_invites_expiry_exact",
            "template_id",
            "uq_partner_invites_token_hash",
            "uq_partner_invites_order_id",
            "uq_partner_invites_job_id",
            "guard_partner_invite_transition",
            "OPEN -> SETTLED_DELETION_PENDING",
            "SETTLED_DELETION_PENDING -> CANCELLED_AND_DELETED",
            "terminal_rebind",
            "partner_consent_case_nonterminal",
            "service_role",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
