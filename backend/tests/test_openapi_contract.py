"""Deterministic public OpenAPI and frontend type-source contract."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.main import app


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "openapi" / "openapi.json"

REQUIRED_OPERATION_IDS = {
    ("get", "/api/v1/auth/me"): "get_current_session_user_api_v1_auth_me_get",
    ("post", "/api/v1/auth/refresh"): "refresh_browser_session_api_v1_auth_refresh_post",
    ("post", "/api/v1/media/uploads"): "upload_media_api_v1_media_uploads_post",
    ("get", "/api/v1/media/{asset_id}"): "read_owner_source_asset_api_v1_media__asset_id__get",
    ("post", "/api/v1/orders/create"): "create_order_api_v1_orders_create_post",
    ("get", "/api/v1/orders/{order_id}"): "get_order_api_v1_orders__order_id__get",
    (
        "get",
        "/api/v1/orders/{order_id}/assets/{asset_id}/download",
    ): "download_order_asset_api_v1_orders__order_id__assets__asset_id__download_get",
    ("get", "/api/v1/templates"): "list_templates_api_v1_templates_get",
    ("get", "/api/v1/credits/packages"): "list_packages_api_v1_credits_packages_get",
    ("post", "/api/v1/payments/checkout"): "create_checkout_api_v1_payments_checkout_post",
    ("get", "/api/v1/subscriptions/plans"): "list_subscription_plans_api_v1_subscriptions_plans_get",
    ("post", "/api/v1/subscriptions/checkout"): "create_subscription_checkout_api_v1_subscriptions_checkout_post",
    ("post", "/api/v1/subscriptions/cancel"): "cancel_my_subscription_api_v1_subscriptions_cancel_post",
    ("get", "/api/v1/account/export"): "export_current_account_api_v1_account_export_get",
    ("post", "/api/v1/partner-invites"): "create_invite_api_v1_partner_invites_post",
    ("post", "/api/v1/partner-invites/accept"): "accept_invite_api_v1_partner_invites_accept_post",
    (
        "post",
        "/api/v1/partner-invites/{invite_id}/order",
    ): "create_invite_order_api_v1_partner_invites__invite_id__order_post",
    (
        "post",
        "/api/v1/partner-invites/{invite_id}/withdraw",
    ): "withdraw_invite_api_v1_partner_invites__invite_id__withdraw_post",
}

PRIVATE_MEDIA_RESPONSE_SCHEMAS = (
    "MediaAssetResponse",
    "MediaDeletionResponse",
    "OrderAssetRead",
    "OrderRead",
    "PartnerInviteSnapshot",
    "UploadBatchResponse",
)

ACCOUNT_EXPORT_FORBIDDEN_FIELDS = {
    "auth_subject",
    "avatar_url",
    "checkout_url",
    "generation_params",
    "metadata_json",
    "object_key",
    "openid",
    "password",
    "payload_json",
    "provider_customer_id",
    "provider_evidence",
    "provider_job_id",
    "provider_payment_id",
    "raw_payload_sha256",
    "request_hash",
    "request_snapshot",
    "stored_response",
    "subject",
    "token_hash",
    "unionid",
}

FORBIDDEN_IDENTITY_FIELDS = {
    "openid",
    "open_id",
    "visitor_id",
    "guest_user_id",
    "anonymous_user_id",
}
FORBIDDEN_MEDIA_FIELDS = {
    "object_key",
    "public_url",
    "image_url",
    "image_urls",
    "source_image_url",
    "source_image_urls",
    "preview_image_url",
    "preview_image_urls",
    "final_image_url",
    "final_image_urls",
}


class OpenApiContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = app.openapi()
        self.schemas = self.spec["components"]["schemas"]

    def test_required_web_operations_have_exact_stable_ids(self) -> None:
        seen: set[str] = set()
        for path_item in self.spec["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "operationId" not in operation:
                    continue
                operation_id = operation["operationId"]
                self.assertNotIn(operation_id, seen)
                seen.add(operation_id)

        for (method, path), expected in REQUIRED_OPERATION_IDS.items():
            with self.subTest(method=method, path=path):
                self.assertEqual(
                    self.spec["paths"][path][method]["operationId"],
                    expected,
                )

    def test_public_schemas_exclude_legacy_identity_ownership(self) -> None:
        for schema_name, schema in self.schemas.items():
            fields = {field.lower() for field in schema.get("properties", {})}
            with self.subTest(schema=schema_name):
                self.assertTrue(fields.isdisjoint(FORBIDDEN_IDENTITY_FIELDS), fields)

    def test_private_media_responses_expose_no_storage_or_public_image_urls(self) -> None:
        for schema_name in PRIVATE_MEDIA_RESPONSE_SCHEMAS:
            fields = {field.lower() for field in self.schemas[schema_name].get("properties", {})}
            with self.subTest(schema=schema_name):
                self.assertTrue(fields.isdisjoint(FORBIDDEN_MEDIA_FIELDS), fields)

    def test_order_settlement_status_is_a_strict_public_enum(self) -> None:
        settlement = self.schemas["OrderRead"]["properties"]["settlement_status"]
        self.assertEqual(
            settlement.get("enum"),
            ["NOT_CHARGED", "CAPTURED", "REFUNDED", "RECONCILING"],
        )
        self.assertIn("settlement_status", self.schemas["OrderRead"]["required"])

    def test_account_export_is_strict_and_exposes_no_internal_or_provider_secrets(self) -> None:
        export_models = {
            name: schema
            for name, schema in self.schemas.items()
            if name == "AccountExport" or name.startswith("AccountExport")
        }
        self.assertGreaterEqual(len(export_models), 10)
        for name, schema in export_models.items():
            fields = {field.lower() for field in schema.get("properties", {})}
            with self.subTest(schema=name):
                self.assertTrue(
                    fields.isdisjoint(ACCOUNT_EXPORT_FORBIDDEN_FIELDS),
                    fields & ACCOUNT_EXPORT_FORBIDDEN_FIELDS,
                )
                for field in fields:
                    self.assertFalse(field.endswith("_url"), field)
                    self.assertFalse(field.endswith("_path"), field)
        self.assertIn(
            "content_sha256",
            self.schemas["AccountExportMedia"]["properties"],
        )

    def test_committed_snapshot_is_exact_deterministic_export(self) -> None:
        from scripts.export_openapi import render_openapi

        first = render_openapi()
        second = render_openapi()
        self.assertEqual(first, second)
        self.assertTrue(OPENAPI_PATH.is_file(), "openapi/openapi.json is missing")
        self.assertEqual(OPENAPI_PATH.read_bytes(), first)
        self.assertEqual(json.loads(first), self.spec)


if __name__ == "__main__":
    unittest.main()
