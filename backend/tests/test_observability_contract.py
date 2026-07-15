"""Structured-event correlation, redaction, and metrics contracts."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ObservabilityContractTest(unittest.TestCase):
    def _module(self):
        try:
            from app.core import observability
        except ImportError as exc:  # precise RED when the contract is absent
            self.fail(f"observability contract is missing: {exc}")
        return observability

    def test_structured_event_keeps_only_allowlisted_correlations(self) -> None:
        observability = self._module()

        event = observability.build_structured_event(
            "provider.completed",
            request_id="req_123",
            user_id="user-123",
            order_id="order-123",
            job_id="job-123",
            attempt_id="attempt-123",
            provider_job_id="provider-123",
            deployment_id="dpl_123",
            git_sha="a" * 40,
        )

        self.assertEqual(event["event"], "provider.completed")
        self.assertEqual(event["request_id"], "req_123")
        self.assertRegex(event["user_id"], r"^uid_[0-9a-f]{24}$")
        self.assertNotEqual(event["user_id"], "user-123")
        self.assertNotIn("timestamp", event)

        with self.assertRaises(observability.ObservabilityContractError):
            observability.build_structured_event("provider.completed", unknown_field="value")

    def test_sensitive_values_are_rejected_before_emission(self) -> None:
        observability = self._module()
        forbidden = {
            "access_token": "bearer-token",
            "refresh_token": "refresh-token",
            "csrf_secret": "csrf-token",
            "email": "person@example.com",
            "image_bytes": b"raw-image",
            "embedding": [0.1, 0.2],
            "object_url": "https://storage.example/private/object.jpg",
            "payment_secret": "payment-token",
            "internal_path": r"D:\\private\\image.jpg",
        }
        for field, value in forbidden.items():
            with self.subTest(field=field), self.assertRaises(
                observability.ObservabilityContractError
            ):
                observability.build_structured_event("unsafe.event", **{field: value})

    def test_mandatory_metrics_cover_the_operational_failure_boundaries(self) -> None:
        observability = self._module()

        self.assertEqual(
            observability.MANDATORY_METRICS,
            frozenset(
                {
                    "auth_rejection_total",
                    "cross_user_denial_total",
                    "upload_rejection_total",
                    "outbox_backlog_count",
                    "outbox_oldest_age_seconds",
                    "queue_latency_seconds",
                    "lease_recovery_total",
                    "provider_latency_seconds",
                    "provider_cost_total",
                    "qa_reject_total",
                    "qa_repair_total",
                    "watermark_failure_total",
                    "download_denial_total",
                    "webhook_unhandled_total",
                    "ledger_reconciliation_failure_total",
                    "deletion_failure_total",
                    "cleanup_last_success_timestamp_seconds",
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
