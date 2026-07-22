"""Account finalization is derived from database and storage facts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_provider_facts import collect_commercial_finalize


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.current: list[dict] = []

    def execute(self, query: str, _params: tuple) -> None:
        if "JOIN account_tombstones" in query:
            name = "closed"
        else:
            raise AssertionError(f"unrecognized Provider collector query: {query}")
        self.current = [dict(self.rows[name])]

    def fetchall(self) -> list[dict]:
        return self.current


def _binding() -> dict:
    return {
        "source_sha": "a" * 40,
        "runtime_bundle_id": "rtb_" + "b" * 64,
        "deployment_id": "dpl-provider",
        "manifest_sha256": "c" * 64,
        "user_subject_hmac_sha256": "d" * 64,
    }


def _rows() -> dict[str, dict]:
    return {
        "closed": {
            "user_id": "user-01",
            "status": "closed",
            "closed_at": NOW,
            "media_cleanup_pending": False,
            "audit_request_id": "request-close-01",
            "active_sessions": 0,
            "accessible_assets": 0,
            "active_acceptance_bindings": 0,
        },
    }


class AcceptanceProviderCollectorTest(unittest.TestCase):
    def test_finalization_requires_zero_session_media_and_binding_residue(self) -> None:
        browser = {
            **_binding(),
            "currency": "USD",
            "cost_cap_minor_units": 1,
            "observations": {
                "account_close_response": True,
                "post_close_session_denied": True,
            },
            "links": {"user_id": "user-01"},
        }
        finalized, facts = collect_commercial_finalize(
            _Cursor(_rows()),
            browser=browser,
            identity_report={"links": {"user_id": "user-01"}},
            storage_absence_report={
                "passed": True,
                "user_subject_hmac_sha256": "d" * 64,
                "verified_asset_count": 2,
                "storage_read_outcome": "NOT_FOUND",
            },
        )
        self.assertTrue(finalized["assertions"]["no_acceptance_binding_residue"])
        self.assertTrue(finalized["assertions"]["account_closed"])
        self.assertEqual(facts["closed"]["accessible_assets"], 0)

        rows = _rows()
        rows["closed"]["accessible_assets"] = 1
        with self.assertRaisesRegex(ValueError, "residue"):
            collect_commercial_finalize(
                _Cursor(rows),
                browser=browser,
                identity_report={"links": {"user_id": "user-01"}},
                storage_absence_report={
                    "passed": True,
                    "user_subject_hmac_sha256": "d" * 64,
                    "verified_asset_count": 2,
                    "storage_read_outcome": "NOT_FOUND",
                },
            )


if __name__ == "__main__":
    unittest.main()
