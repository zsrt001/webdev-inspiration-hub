"""Generation analytics must use normalized attempts and immutable verdicts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.generation_attempt import (  # noqa: E402
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.order import OrderStatus  # noqa: E402
from app.models.qa_verdict import QaDecision  # noqa: E402
from app.services.analytics_reporting_service import (  # noqa: E402
    NormalizedQualityFact,
    _build_quality_dashboard_from_facts,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _fact(
    *,
    order_id: uuid.UUID,
    template_id: str,
    attempt_number: int,
    kind: GenerationAttemptKind,
    decision: QaDecision,
    reasons: tuple[str, ...] = (),
    identity_score: float = 1.0,
    cost_minor_units: int = 0,
) -> NormalizedQualityFact:
    return NormalizedQualityFact(
        order_id=order_id,
        created_at=NOW,
        template_id=template_id,
        order_status=OrderStatus.READY,
        attempt_number=attempt_number,
        attempt_kind=kind,
        attempt_status=GenerationAttemptStatus.FINISHED,
        cost_minor_units=cost_minor_units,
        cost_currency="USD",
        decision=decision,
        reasons=reasons,
        scores={"identity": identity_score},
    )


class AnalyticsReportingServiceTest(unittest.TestCase):
    def test_quality_dashboard_derives_repair_reasons_and_cost_from_facts(self) -> None:
        repaired_order = uuid.uuid4()
        passing_order = uuid.uuid4()
        facts = [
            _fact(
                order_id=repaired_order,
                template_id="royal_castle",
                attempt_number=1,
                kind=GenerationAttemptKind.INITIAL,
                decision=QaDecision.REPAIR,
                reasons=("face_underexposed",),
                identity_score=0.91,
                cost_minor_units=12,
            ),
            _fact(
                order_id=repaired_order,
                template_id="royal_castle",
                attempt_number=2,
                kind=GenerationAttemptKind.REPAIR,
                decision=QaDecision.PASS,
                identity_score=0.95,
                cost_minor_units=4,
            ),
            _fact(
                order_id=passing_order,
                template_id="solo_royal_castle",
                attempt_number=1,
                kind=GenerationAttemptKind.INITIAL,
                decision=QaDecision.REJECT,
                reasons=("identity_similarity_low",),
                identity_score=0.42,
                cost_minor_units=9,
            ),
        ]

        dashboard = _build_quality_dashboard_from_facts(facts)

        totals = dashboard["totals"]
        self.assertEqual(totals["orders"], 2)
        self.assertEqual(totals["qa_failed_orders"], 2)
        self.assertEqual(totals["identity_failed_orders"], 1)
        self.assertEqual(totals["repair_round_sum"], 1)
        self.assertEqual(totals["provider_cost_minor_units"], {"USD": 25})

        reasons = {item["reason"]: item for item in dashboard["failure_reasons"]}
        self.assertEqual(reasons["face_underexposed"]["group"], "lighting")
        self.assertEqual(reasons["identity_similarity_low"]["group"], "identity")

        rounds = {item["round"]: item for item in dashboard["repair_rounds"]}
        self.assertEqual(rounds[1]["attempts"], 2)
        self.assertEqual(rounds[2]["success_rate"], 1.0)
        self.assertEqual(rounds[1]["avg_identity_score"], 0.67)

    def test_legacy_debug_params_and_credit_policy_are_absent(self) -> None:
        service = (BACKEND_DIR / "app/services/analytics_reporting_service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "Order.generation_params",
            "image_edit_rounds",
            "qa_last_reasons",
            "qa_attempt_count",
            "generation_credit_policy",
        ):
            self.assertNotIn(forbidden, service)
        self.assertFalse(
            (BACKEND_DIR / "app/services/generation_credit_policy.py").exists()
        )


if __name__ == "__main__":
    unittest.main()
