"""Acceptance identity binding privacy and single-consumption contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]


def _service():
    path = ROOT / "backend" / "app" / "services" / "acceptance_identity_service.py"
    if not path.exists():
        raise AssertionError("acceptance identity service is missing")
    return importlib.import_module("app.services.acceptance_identity_service")


class AcceptanceIdentityBindingTest(unittest.TestCase):
    def test_subject_hmac_is_keyed_and_never_returns_raw_identity(self) -> None:
        service = _service()
        first = service.compute_subject_hmac("key-a", "google", "subject-123")
        second = service.compute_subject_hmac("key-b", "google", "subject-123")
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)
        self.assertNotIn("subject-123", first)

    def test_binding_input_requires_provider_subject_coordinates_reason_and_bounded_expiry(self) -> None:
        service = _service()
        now = datetime.now(timezone.utc)
        valid = {
            "provider": "google",
            "subject": "subject-123",
            "environment": "preview",
            "deployment_id": "dpl_preview",
            "expires_at": now + timedelta(hours=2),
            "actor": "admin-user:test",
            "reason": "identity preview",
        }
        service.validate_binding_request(now=now, **valid)
        for key in ("provider", "subject", "environment", "deployment_id", "actor", "reason"):
            invalid = dict(valid)
            invalid[key] = ""
            with self.assertRaises(ValueError, msg=key):
                service.validate_binding_request(now=now, **invalid)
        invalid = dict(valid)
        invalid["expires_at"] = now + timedelta(seconds=86401)
        with self.assertRaises(ValueError):
            service.validate_binding_request(now=now, **invalid)

    def test_model_has_no_raw_subject_or_email_column(self) -> None:
        module = importlib.import_module("app.models.acceptance_identity_binding")
        columns = set(module.AcceptanceIdentityBinding.__table__.columns.keys())
        self.assertIn("subject_hmac", columns)
        self.assertFalse({"subject", "provider_subject", "email"} & columns)
        migration = (
            ROOT / "backend" / "alembic" / "versions" / "20260710_0013_ops_feature_flags.py"
        ).read_text(encoding="utf-8")
        self.assertIn("acceptance identity coordinates are immutable", migration)
        self.assertIn("consumed acceptance identity is immutable", migration)


class AcceptanceIdentityConsumptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_consumption_is_one_time_and_bound_to_exact_coordinates(self) -> None:
        service = _service()
        now = datetime.now(timezone.utc)
        local_user_id = uuid4()
        row = SimpleNamespace(
            consumed_user_id=None,
            consumed_at=None,
            expires_at=now + timedelta(minutes=15),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: row)

        consumed = await service.consume_acceptance_binding(
            db,
            provider="google",
            subject="subject-123",
            environment="preview",
            deployment_id="dpl_preview",
            local_user_id=local_user_id,
            now=now,
            hmac_key="test-hmac-key",
        )
        self.assertTrue(consumed)
        self.assertEqual(row.consumed_user_id, local_user_id)
        self.assertEqual(row.consumed_at, now)

        self.assertFalse(await service.consume_binding_row(row, local_user_id, now=now))


if __name__ == "__main__":
    unittest.main()
