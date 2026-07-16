"""Financial reporting must read immutable commercial facts only."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def _async_function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


class FinancialReportingAuthorityTest(unittest.TestCase):
    def test_admin_financial_totals_ignore_mutable_order_and_live_job_fields(self) -> None:
        segment = _async_function_source(
            ROOT / "backend/app/services/admin_service.py",
            "get_dashboard_stats",
        )
        self.assertIn("CreditTransaction", segment)
        self.assertNotIn("Order.generation_params", segment)
        self.assertNotIn("billable_generation_credits", segment)
        self.assertNotIn("LivePortraitJob.credits_cost", segment)
        self.assertNotIn("CreditPurchase.price_cents", segment)
        self.assertNotIn("fallback_amount", segment)

    def test_ops_cost_series_uses_ledger_not_generation_params_or_price_fallbacks(self) -> None:
        segment = _async_function_source(
            ROOT / "backend/app/services/ops_monitoring_service.py",
            "get_ops_monitoring_summary",
        )
        self.assertIn("CreditTransaction", segment)
        self.assertNotIn("Order.generation_params", segment)
        self.assertNotIn("billable_generation_credits", segment)
        self.assertNotIn("LivePortraitJob.credits_cost", segment)
        self.assertNotIn("CreditPurchase.price_cents", segment)


if __name__ == "__main__":
    unittest.main()
