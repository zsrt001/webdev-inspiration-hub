from __future__ import annotations

from datetime import timezone
import unittest
from unittest.mock import AsyncMock, patch


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _StatementRecordingSession:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(0)


class OpsAlertServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_timestamp_cutoffs_match_their_postgres_column_types(self) -> None:
        from app.services import ops_alert_service

        session = _StatementRecordingSession()
        with patch.object(
            ops_alert_service,
            "get_ops_monitoring_summary",
            new=AsyncMock(return_value={}),
        ):
            await ops_alert_service.get_ops_alerts(session, days=1)

        self.assertEqual(len(session.statements), 3)
        lead_cutoff = list(session.statements[0]._where_criteria)[0].right.value
        order_cutoff = list(session.statements[1]._where_criteria)[0].right.value
        live_cutoff = list(session.statements[2]._where_criteria)[0].right.value

        self.assertIsNone(lead_cutoff.tzinfo)
        self.assertIs(order_cutoff.tzinfo, timezone.utc)
        self.assertIs(live_cutoff.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
