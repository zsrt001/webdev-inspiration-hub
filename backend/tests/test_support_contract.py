"""Fail-closed public support-channel configuration contracts."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.core import runtime_checks  # noqa: E402
from app.services import legal_policy_service, ops_config_service  # noqa: E402


class SupportConfigContractTest(unittest.TestCase):
    def test_support_is_unavailable_by_default_without_a_hardcoded_contact(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(
            settings.public_support_contact,
            {"available": False, "email": "", "url": ""},
        )
        self.assertIn("SUPPORT_EMAIL or SUPPORT_URL is required", settings.support_contact_config_errors)
        self.assertIn(
            "SUPPORT_MONITORED must confirm an actively monitored support channel",
            settings.support_contact_config_errors,
        )

    def test_canonical_environment_aliases_publish_a_confirmed_email(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SUPPORT_EMAIL": "Support@Example.com",
                "SUPPORT_MONITORED": "true",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.support_contact_config_errors, [])
        self.assertEqual(
            settings.public_support_contact,
            {"available": True, "email": "Support@example.com", "url": ""},
        )

    def test_legacy_environment_aliases_remain_compatible(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SUPPORT_CONTACT_URL": "https://support.example.com/tickets?source=vowpic",
                "SUPPORT_CONTACT_MONITORED": "true",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.support_contact_config_errors, [])
        self.assertEqual(
            settings.public_support_contact["url"],
            "https://support.example.com/tickets?source=vowpic",
        )

    def test_unconfirmed_or_unsafe_contacts_fail_closed(self) -> None:
        cases = (
            Settings(_env_file=None, support_contact_email="support@example.com"),
            Settings(
                _env_file=None,
                support_contact_url="http://support.example.com/tickets",
                support_contact_monitored=True,
            ),
            Settings(
                _env_file=None,
                support_contact_url="https://user:secret@support.example.com/tickets",
                support_contact_monitored=True,
            ),
            Settings(
                _env_file=None,
                support_contact_url="https://127.0.0.1/tickets",
                support_contact_monitored=True,
            ),
        )
        for settings in cases:
            with self.subTest(settings=settings):
                self.assertFalse(settings.public_support_contact["available"])
                self.assertTrue(settings.support_contact_config_errors)

    def test_public_ops_config_exposes_only_the_validated_contact(self) -> None:
        unavailable = Settings(
            _env_file=None,
            support_contact_email="support@example.com",
            support_contact_monitored=False,
        )
        available = Settings(
            _env_file=None,
            support_contact_url="https://support.example.com/tickets",
            support_contact_monitored=True,
        )
        with patch.object(ops_config_service, "settings", unavailable):
            self.assertFalse(ops_config_service.get_public_ops_config()["support"]["available"])
        with patch.object(ops_config_service, "settings", available):
            self.assertEqual(
                ops_config_service.get_public_ops_config()["support"],
                {"available": True, "email": "", "url": "https://support.example.com/tickets"},
            )

    def test_legal_policy_never_falls_back_to_an_unverified_manual_contact(self) -> None:
        settings = Settings(
            _env_file=None,
            manual_payment_contact="unverified@example.com",
        )
        with patch.object(legal_policy_service, "get_settings", return_value=settings):
            refunds = legal_policy_service.get_legal_policies()["refunds"]

        self.assertFalse(refunds["support_available"])
        self.assertEqual(refunds["support_contact"], "")
        self.assertIn("No verified support channel", refunds["summary"])

    def test_commercial_readiness_requires_the_confirmed_validated_channel(self) -> None:
        unconfirmed = Settings(
            _env_file=None,
            release_role="COMMERCIAL_7A",
            support_contact_email="support@example.com",
            support_contact_monitored=False,
        )
        confirmed = Settings(
            _env_file=None,
            release_role="COMMERCIAL_7A",
            support_contact_email="support@example.com",
            support_contact_monitored=True,
        )
        with patch.object(runtime_checks, "settings", unconfirmed):
            errors = runtime_checks.validate_commercial_config_values()
        self.assertIn(
            "SUPPORT_MONITORED must confirm an actively monitored support channel",
            errors,
        )
        with patch.object(runtime_checks, "settings", confirmed):
            errors = runtime_checks.validate_commercial_config_values()
        self.assertFalse(any(error.startswith("SUPPORT_") for error in errors))

    def test_safe_baseline_hides_missing_support_without_blocking_browse_readiness(self) -> None:
        safe_baseline = Settings(
            _env_file=None,
            release_role="SAFE_BASELINE",
        )
        with patch.object(runtime_checks, "settings", safe_baseline):
            errors = runtime_checks.validate_commercial_config_values()

        self.assertFalse(any(error.startswith("SUPPORT_") for error in errors))
        self.assertFalse(safe_baseline.public_support_contact["available"])


if __name__ == "__main__":
    unittest.main()
