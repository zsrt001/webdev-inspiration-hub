"""Task-28 identity, commercial, and generation migration contracts."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))


class BackfillClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.identities = importlib.import_module(
            "scripts.release.backfill_identities"
        )
        cls.commercial = importlib.import_module(
            "scripts.release.backfill_commercial_facts"
        )
        cls.generation = importlib.import_module(
            "scripts.release.backfill_generation_facts"
        )

    @staticmethod
    def _identity_row(**overrides):
        row = {
            "already_merged": False,
            "auth_provider": "supabase",
            "auth_subject": "subject-1",
            "active_identity_count": 1,
            "subject_owner_count": 1,
            "has_blocking_facts": False,
        }
        row.update(overrides)
        return row

    def test_every_identity_has_one_explicit_disposition(self) -> None:
        cases = (
            (self._identity_row(), "NORMALIZED"),
            (self._identity_row(already_merged=True), "MERGED"),
            (
                self._identity_row(active_identity_count=2),
                "QUARANTINED_BLOCKING",
            ),
            (
                self._identity_row(
                    auth_provider="legacy",
                    auth_subject="",
                    active_identity_count=0,
                    subject_owner_count=0,
                ),
                "SOFT_CLOSED_TOMBSTONED",
            ),
            (
                self._identity_row(
                    auth_provider=None,
                    auth_subject=None,
                    active_identity_count=0,
                    subject_owner_count=0,
                    has_blocking_facts=True,
                ),
                "QUARANTINED_BLOCKING",
            ),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    self.identities.classify_identity(row),
                    expected,
                )

    def test_generation_never_invents_runtime_coordinates(self) -> None:
        for status in ("COMPLETED", "READY", "FAILED", "CANCELLED", "DELETED"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.generation.classify_generation(status),
                    "legacy_terminal_evidence",
                )
        for status in ("CREATED", "QUEUED", "GENERATING", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.generation.classify_generation(status),
                    "quarantined_runnable",
                )
        source = (
            ROOT / "scripts/release/backfill_generation_facts.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"invented_runtime_stamps": 0', source)
        self.assertNotIn("worker_deployment_id =", source)
        self.assertNotIn("runtime_bundle_id =", source)
        self.assertIn("payload_version = 'legacy-evidence.v1'", source)
        self.assertIn("payload_version = 'legacy-quarantined.v1'", source)
        self.assertIn("SET status = 'CANCELLED'", source)
        self.assertIn("lease_owner = NULL", source)

    def test_commercial_backfill_is_idempotent_and_does_not_fabricate_passes(
        self,
    ) -> None:
        source = (
            ROOT / "scripts/release/backfill_commercial_facts.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("ON CONFLICT"), 2)
        self.assertIn("'LEGACY_POOL'", source)
        self.assertIn("'LEGACY_UNLINKED_DEBIT'", source)
        self.assertIn("'LEGACY_UNVERIFIED'", source)
        self.assertIn('"fabricated_purchase_count": 0', source)
        self.assertIn('"fabricated_qa_pass_count": 0', source)
        self.assertNotIn("INSERT INTO credit_purchases", source)
        self.assertNotIn("INSERT INTO qa_verdicts", source)

    def test_case_hash_is_stable_and_namespaced(self) -> None:
        first = self.commercial._case_hash("legacy_unlinked", "row-1")
        again = self.commercial._case_hash("legacy_unlinked", "row-1")
        other = self.commercial._case_hash("legacy_pool", "row-1")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_blocking_identities_are_durably_login_disabled(self) -> None:
        source = (
            ROOT / "scripts/release/backfill_identities.py"
        ).read_text(encoding="utf-8")
        quarantine = source.index("SET status = 'quarantined'")
        revoke_session = source.index(
            "SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)",
            quarantine,
        )
        self.assertGreater(revoke_session, quarantine)
        self.assertIn("SOFT_CLOSED_TOMBSTONED", source)
        self.assertIn("INSERT INTO account_tombstones", source)
        self.assertNotIn("provider, subject) VALUES", source)


if __name__ == "__main__":
    unittest.main()
