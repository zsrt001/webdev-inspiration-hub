"""Provider recovery and account finalization are DB-derived, never prewritten."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from scripts.release._acceptance_provider_facts import (
    collect_commercial_finalize,
    collect_provider_unknown_complete,
    collect_provider_unknown_queue,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.current: list[dict] = []

    def execute(self, query: str, _params: tuple) -> None:
        if "FROM release_activations" in query:
            name = "activation"
        elif "AS initial_submit_count" in query:
            name = "completed"
        elif "JOIN generation_jobs" in query:
            name = "queued"
        elif "JOIN account_tombstones" in query:
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


def _contracts() -> dict:
    return {
        "schema": "vowpic.provider-contracts.v1",
        "contracts": {
            "EVOLINK_SUBMISSION_RECONCILIATION": {
                "state": "VERIFIED",
                "tested_source_sha": "a" * 40,
                "test_evidence_sha256": "e" * 64,
                "endpoint_schema_sha256": "f" * 64,
            }
        },
    }


def _rows() -> dict[str, dict]:
    return {
        "activation": {
            "id": "activation-01",
            "workflow_run_id": "run-01",
            "workflow_attempt": 1,
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "manifest_sha256": "c" * 64,
            "api_deployment_id": "dpl-provider",
            "worker_deployment_id": "worker-01",
            "worker_image_digest": "sha256:" + "9" * 64,
        },
        "queued": {
            "order_id": "provider-order-01",
            "user_id": "user-01",
            "order_status": "QUEUED",
            "reservation_id": "provider-reservation-01",
            "job_id": "provider-job-01",
            "submission_correlation_id": "correlation-01",
            "job_status": "QUEUED",
            "attempt_id": "provider-attempt-01",
            "api_deployment_id": "dpl-provider",
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "expected_worker_image_digest": "sha256:" + "9" * 64,
            "attempt_status": "PREPARED",
            "client_request_id": "correlation-01",
            "provider_job_id": None,
            "submission_accounting_state": "NOT_CAPTURED",
            "cost_minor_units": 0,
            "cost_currency": None,
        },
        "completed": {
            "order_id": "provider-order-01",
            "user_id": "user-01",
            "order_status": "READY",
            "job_id": "provider-job-01",
            "job_status": "FINISHED",
            "attempt_id": "provider-attempt-01",
            "attempt_status": "FINISHED",
            "provider_job_id": "evolink-task-01",
            "submission_accounting_state": "CAPTURED",
            "cost_minor_units": 2,
            "initial_submit_count": 1,
            "pass_count": 1,
        },
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
    def test_queue_and_completion_bind_the_same_single_submit(self) -> None:
        queue_browser = {
            **_binding(),
            "currency": "USD",
            "cost_cap_minor_units": 5,
            "observations": {"order_accepted": True},
            "links": {"user_id": "user-01", "order_id": "provider-order-01"},
        }
        commercial = {
            "passed": True,
            "links": {"user_id": "user-01"},
        }
        queued, queue_facts = collect_provider_unknown_queue(
            _Cursor(_rows()),
            browser=queue_browser,
            commercial=commercial,
            provider_contracts=_contracts(),
        )
        self.assertEqual(queued["links"]["user_id"], "user-01")
        self.assertTrue(queued["assertions"]["fault_intent_coordinate_prepared"])
        self.assertEqual(queue_facts["queued"]["provider_job_id"], None)

        complete_browser = {
            **_binding(),
            "currency": "USD",
            "cost_cap_minor_units": 5,
            "observations": {
                "order_ready": True,
                "same_order_visible": True,
            },
            "links": {"user_id": "user-01", "order_id": "provider-order-01"},
        }
        completed, complete_facts = collect_provider_unknown_complete(
            _Cursor(_rows()),
            browser=complete_browser,
            queued_report={"passed": True, "links": queued["links"]},
        )
        self.assertEqual(
            completed["links"]["fault_intent_id"],
            queued["links"]["fault_intent_id"],
        )
        self.assertTrue(completed["assertions"]["single_submit"])
        self.assertEqual(complete_facts["completed"]["initial_submit_count"], 1)

    def test_unverified_evolink_contract_cannot_queue_the_fault_case(self) -> None:
        current = json.loads(
            (ROOT / "release/provider-contracts.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ValueError, "not VERIFIED"):
            collect_provider_unknown_queue(
                _Cursor(_rows()),
                browser={
                    **_binding(),
                    "currency": "USD",
                    "cost_cap_minor_units": 5,
                    "observations": {"order_accepted": True},
                    "links": {
                        "user_id": "user-01",
                        "order_id": "provider-order-01",
                    },
                },
                commercial={"links": {"user_id": "user-01"}},
                provider_contracts=current,
            )

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
            commercial_report={"links": {"user_id": "user-01"}},
            provider_report={"passed": True},
            storage_absence_report={
                "passed": True,
                "user_subject_hmac_sha256": "d" * 64,
                "verified_asset_count": 2,
                "storage_read_outcome": "NOT_FOUND",
            },
        )
        self.assertTrue(finalized["assertions"]["no_acceptance_binding_residue"])
        self.assertEqual(facts["closed"]["accessible_assets"], 0)

        rows = _rows()
        rows["closed"]["accessible_assets"] = 1
        with self.assertRaisesRegex(ValueError, "residue"):
            collect_commercial_finalize(
                _Cursor(rows),
                browser=browser,
                commercial_report={"links": {"user_id": "user-01"}},
                provider_report={"passed": True},
                storage_absence_report={
                    "passed": True,
                    "user_subject_hmac_sha256": "d" * 64,
                    "verified_asset_count": 2,
                    "storage_read_outcome": "NOT_FOUND",
                },
            )


if __name__ == "__main__":
    unittest.main()
