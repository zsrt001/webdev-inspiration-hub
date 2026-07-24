"""Fail-closed classification and workflow contract for legacy outbox rows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.outbox_event import OutboxEvent, OutboxEventStatus  # noqa: E402
from app.services.legacy_outbox_retirement_service import (  # noqa: E402
    classify_legacy_outbox_event,
)


NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _event(
    *,
    aggregate_type: str,
    event_type: str,
    payload_version: str,
    payload_key: str,
) -> OutboxEvent:
    aggregate_id = uuid.uuid4()
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        dedupe_key=f"legacy:{aggregate_id}",
        payload_version=payload_version,
        payload_json={
            payload_key: str(aggregate_id),
            "payload_version": payload_version,
        },
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=NOW,
        fencing_token=0,
    )


class LegacyOutboxRetirementTest(unittest.TestCase):
    def test_empty_inventory_has_no_rows_to_retire(self) -> None:
        from app.services.legacy_outbox_retirement_service import _report

        report = _report(
            source_sha="a" * 40,
            mode="inventory",
            decisions=[],
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["active_count"], 0)

    def test_applied_payment_and_accounted_generation_are_retirable(self) -> None:
        payment = _event(
            aggregate_type="payment_event",
            event_type="payment.event.received",
            payload_version="vowpic.payment-event.v1",
            payload_key="payment_event_id",
        )
        job = _event(
            aggregate_type="generation_job",
            event_type="GENERATION_JOB_CREATED",
            payload_version="generation-job.v1",
            payload_key="job_id",
        )

        payment_decision = classify_legacy_outbox_event(
            payment,
            SimpleNamespace(id=payment.aggregate_id, processing_state="APPLIED"),
        )
        job_decision = classify_legacy_outbox_event(
            job,
            SimpleNamespace(
                id=job.aggregate_id,
                status="FINISHED",
                settlement_status="CAPTURED",
                delivery_status="READY",
            ),
        )

        self.assertTrue(payment_decision.retirable)
        self.assertTrue(job_decision.retirable)

    def test_queued_unknown_or_malformed_rows_remain_blockers(self) -> None:
        job = _event(
            aggregate_type="generation_job",
            event_type="GENERATION_JOB_CREATED",
            payload_version="generation-job.v1",
            payload_key="job_id",
        )
        unknown = _event(
            aggregate_type="unknown",
            event_type="UNKNOWN",
            payload_version="unknown.v1",
            payload_key="id",
        )
        malformed = _event(
            aggregate_type="payment_event",
            event_type="payment.event.received",
            payload_version="vowpic.payment-event.v1",
            payload_key="payment_event_id",
        )
        malformed.payload_json = {"payment_event_id": str(uuid.uuid4())}

        decisions = (
            classify_legacy_outbox_event(
                job,
                SimpleNamespace(
                    id=job.aggregate_id,
                    status="QUEUED",
                    settlement_status="RESERVED",
                    delivery_status="PENDING",
                ),
            ),
            classify_legacy_outbox_event(unknown, None),
            classify_legacy_outbox_event(malformed, None),
        )

        self.assertTrue(all(not decision.retirable for decision in decisions))
        self.assertEqual(
            {decision.reason for decision in decisions},
            {
                "generation_job_requires_backend_recovery_or_manual_settlement",
                "unknown_legacy_outbox_contract",
                "payment_event_envelope_invalid",
            },
        )

    def test_production_workflow_recovers_then_retires_before_observation(self) -> None:
        workflow = (ROOT / ".github/workflows/production-release.yml").read_text(
            encoding="utf-8"
        )
        recovery = workflow.index("Drain recoverable legacy generation work")
        inventory = workflow.index("Inventory and retire reconciled legacy outbox")
        observation = workflow.index("Start one durable observation row and exit")

        self.assertLess(recovery, inventory)
        self.assertLess(inventory, observation)
        self.assertIn("retire_legacy_outbox.py inventory", workflow)
        self.assertIn("retire_legacy_outbox.py apply", workflow)
        self.assertIn("PRODUCTION_READ_ONLY_DATABASE_URL", workflow)
        self.assertIn("PRODUCTION_MIGRATION_DATABASE_URL", workflow)


if __name__ == "__main__":
    unittest.main()
