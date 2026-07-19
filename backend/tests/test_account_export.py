"""Account-export ownership, projection, and HTTP contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.main import app
from app.routers.account_data import export_current_account
from app.services.account_export_service import (
    AccountExportError,
    _assemble_account_export,
    build_account_export,
    reject_forbidden_export_keys,
)


NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


def _id(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _user(user_id: uuid.UUID):
    return SimpleNamespace(
        id=user_id,
        username="owner",
        email="owner@example.test",
        nickname="Owner",
        role="user",
        status="active",
        last_login_at=NOW,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW,
    )


def _empty_export(**overrides):
    user_id = overrides.pop("requested_user_id", _id(1))
    payload = {
        "canonical_user_id": _id(1),
        "requested_user_id": user_id,
        "users": [_user(_id(1))],
        "merges": [],
        "identities": [],
        "orders": [],
        "ledger": [],
        "purchases": [],
        "refunds": [],
        "disputes": [],
        "subscriptions": [],
        "invoices": [],
        "adjustments": [],
        "invites": [],
        "invite_events": [],
        "consent_cases": [],
        "media": [],
        "tombstones": [],
        "generated_at": NOW,
        "export_id": _id(99),
    }
    payload.update(overrides)
    return _assemble_account_export(**payload)


class AccountExportProjectionTest(unittest.TestCase):
    def test_minimal_export_is_strict_user_owned_and_secret_free(self) -> None:
        export = _empty_export()
        payload = export.model_dump(mode="json")
        self.assertEqual(export.schema_version, "account-export.v1")
        self.assertEqual(export.canonical_user_id, _id(1))
        self.assertEqual(export.included_user_ids, (_id(1),))
        self.assertEqual(export.profile.email, "owner@example.test")
        reject_forbidden_export_keys(payload)
        serialized = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "auth_subject",
            "object_key",
            "password",
            "provider_customer_id",
            "raw_payload_sha256",
            "token_hash",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_merge_linked_history_is_included_but_unrelated_user_is_rejected(self) -> None:
        merge = SimpleNamespace(
            id=_id(10),
            canonical_user_id=_id(1),
            legacy_user_id=_id(2),
            created_at=NOW - timedelta(days=1),
        )
        export = _empty_export(
            requested_user_id=_id(2),
            users=[_user(_id(1)), _user(_id(2))],
            merges=[merge],
        )
        self.assertEqual(export.included_user_ids, (_id(1), _id(2)))
        self.assertEqual(export.merged_accounts[0].legacy_user_id, _id(2))

        with self.assertRaisesRegex(
            AccountExportError, "account_export_user_scope_drift"
        ):
            _empty_export(
                users=[_user(_id(1)), _user(_id(3))],
            )

    def test_cross_user_fact_cannot_enter_export(self) -> None:
        foreign_identity = SimpleNamespace(
            id=_id(20),
            user_id=_id(3),
            provider="supabase",
            revoked_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        with self.assertRaisesRegex(
            AccountExportError, "account_export_identity_scope_drift"
        ):
            _empty_export(identities=[foreign_identity])

    def test_media_exposes_checksum_metadata_without_storage_location(self) -> None:
        asset = SimpleNamespace(
            id=_id(30),
            owner_user_id=_id(1),
            upload_batch_id=_id(31),
            order_id=None,
            job_id=None,
            parent_asset_id=None,
            role="source",
            status="ACTIVE",
            sha256="a" * 64,
            mime_type="image/jpeg",
            byte_size=1024,
            width=512,
            height=512,
            expires_at=NOW + timedelta(days=7),
            read_revoked_at=None,
            deletion_reason=None,
            deleted_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        export = _empty_export(media=[asset])
        self.assertEqual(export.media[0].content_sha256, "a" * 64)
        self.assertFalse(hasattr(export.media[0], "object_key"))
        self.assertEqual(export.retention[0].active_media_count, 1)

    def test_forbidden_nested_key_or_url_value_fails_closed(self) -> None:
        with self.assertRaisesRegex(AccountExportError, "forbidden_export_key"):
            reject_forbidden_export_keys({"nested": {"token_hash": "a" * 64}})
        with self.assertRaisesRegex(AccountExportError, "forbidden_export_url_value"):
            reject_forbidden_export_keys({"label": "https://private.example/object"})
        reject_forbidden_export_keys({"content_sha256": "a" * 64})

    def test_database_projection_never_selects_forbidden_source_columns(self) -> None:
        source = inspect.getsource(build_account_export)
        for forbidden_column in (
            "User.password",
            "User.auth_subject",
            "User.openid",
            "User.unionid",
            "User.avatar_url",
            "UserIdentity.subject",
            "UserIdentity.verified_email_snapshot",
            "Order.generation_params",
            "Order.source_image_urls",
            "CreditTransaction.metadata_json",
            "CreditPurchase.checkout_url",
            "CreditPurchase.provider_payment_id",
            "UserSubscription.provider_customer_id",
            "SubscriptionInvoice.raw_payload_sha256",
            "PartnerInvite.token_hash",
            "PartnerInviteEvent.details_json",
            "MediaAsset.object_key",
        ):
            with self.subTest(column=forbidden_column):
                self.assertNotIn(forbidden_column, source)


class AccountExportHttpContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_route_uses_only_cookie_user_and_returns_private_attachment(self) -> None:
        export = _empty_export()
        user = SimpleNamespace(id=_id(1))
        db = object()
        with patch(
            "app.routers.account_data.build_account_export",
            new=AsyncMock(return_value=export),
        ) as builder:
            response = await export_current_account(current_user=user, db=db)
        builder.assert_awaited_once_with(db, _id(1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertIn(str(export.export_id), response.headers["content-disposition"])
        body = json.loads(bytes(response.body))
        self.assertEqual(body["canonical_user_id"], str(_id(1)))

    def test_openapi_exposes_exact_authenticated_export_contract(self) -> None:
        spec = app.openapi()
        operation = spec["paths"]["/api/v1/account/export"]["get"]
        self.assertEqual(
            operation["operationId"],
            "export_current_account_api_v1_account_export_get",
        )
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response_schema["$ref"], "#/components/schemas/AccountExport")
        properties = spec["components"]["schemas"]["AccountExport"]["properties"]
        self.assertNotIn("user_id", operation.get("parameters", {}))
        self.assertIn("export_id", properties)
        self.assertIn("included_user_ids", properties)


if __name__ == "__main__":
    unittest.main()
