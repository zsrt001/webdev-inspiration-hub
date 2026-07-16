"""Partner Invite HTTP surface is authenticated, typed, and URL-free."""

from __future__ import annotations

import unittest

from app.main import app
from app.schemas.partner_invite import PartnerInviteOrderRequest
from tests.route_contract import effective_operations


class PartnerInviteRoutesTest(unittest.TestCase):
    def test_exact_authenticated_partner_routes_are_registered(self) -> None:
        operations = effective_operations(app)
        for operation in (
            ("POST", "/api/v1/partner-invites"),
            ("POST", "/api/v1/partner-invites/accept"),
            ("GET", "/api/v1/partner-invites/{invite_id}"),
            ("POST", "/api/v1/partner-invites/{invite_id}/consent"),
            ("POST", "/api/v1/partner-invites/{invite_id}/order"),
            ("POST", "/api/v1/partner-invites/{invite_id}/revoke"),
            ("POST", "/api/v1/partner-invites/{invite_id}/withdraw"),
        ):
            self.assertIn(operation, operations)

    def test_partner_order_request_accepts_no_price_partner_asset_or_funding_roots(self) -> None:
        self.assertEqual(
            set(PartnerInviteOrderRequest.model_fields),
            {"expected_version", "host_asset_id", "consent_event_id"},
        )
        schema = app.openapi()
        operation = schema["paths"]["/api/v1/partner-invites/{invite_id}/order"]["post"]
        rendered = str(operation).lower()
        for forbidden in (
            "price",
            "partner_asset",
            "funding_root",
            "provider",
            "object_key",
            "image_url",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
