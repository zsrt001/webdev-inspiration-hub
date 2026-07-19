"""Read-only, redacted production inventory contracts."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]


def _service():
    path = ROOT / "backend" / "app" / "services" / "production_inventory_service.py"
    if not path.exists():
        raise AssertionError("production inventory service is missing")
    return importlib.import_module("app.services.production_inventory_service")


def _flatten_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _flatten_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_strings(item)
    elif isinstance(value, str):
        yield value


def _safe_read_only_proof():
    return {
        "authenticated_role_name": "vowpic_inventory_login",
        "role_name": "vowpic_inventory_login",
        "transaction_read_only": True,
        "default_transaction_read_only": True,
        "role_superuser": False,
        "role_create_db": False,
        "role_create_role": False,
        "role_replication": False,
        "role_bypass_rls": False,
        "role_membership_count": 0,
        "owned_object_count": 0,
        "inventory_table_count": 12,
        "readable_inventory_table_count": 12,
        "inventory_sequence_count": 2,
        "readable_inventory_sequence_count": 2,
        "rls_table_count": 4,
        "inventory_select_policy_count": 4,
        "invalid_inventory_policy_count": 0,
        "writable_table_count": 0,
        "write_probe_sqlstate": "25006",
    }


class ProductionInventoryReportTest(unittest.TestCase):
    def test_legacy_0012_shape_does_not_reference_columns_added_by_0013(self) -> None:
        service = _service()
        schema = {
            "users": {"id", "openid", "auth_provider", "auth_subject", "email"},
            "orders": {"user_id"},
            "user_credits": {"user_id", "balance"},
            "subscription_credit_grants": {"user_id"},
        }
        user_sql = service.build_user_inventory_sql(schema)
        self.assertNotIn("users.username", user_sql)
        self.assertNotIn("users.password", user_sql)
        self.assertNotIn("FROM user_subscriptions", user_sql)
        self.assertIn("0::bigint AS legacy_active_subscriptions", user_sql)

        object_sql = service.build_object_reference_sql(schema)
        self.assertNotIn("users.avatar_url", object_sql)
        self.assertNotIn("credit_purchases.checkout_url", object_sql)

    def test_report_contains_required_conflict_counts_without_sensitive_rows(self) -> None:
        service = _service()
        report = service.ProductionInventoryReport(
            schema_revision="20260710_0013",
            source_database_identity_hmac_sha256="f" * 64,
            users={
                "total": 3,
                "guest": 1,
                "password": 0,
                "other_retired_provider": 0,
                "visitor": 1,
                "missing_subject": 1,
                "duplicate_email_groups": 1,
                "duplicate_subject_groups": 0,
                "asset_owners_unknown": 0,
            },
            ledger={"balance_mismatch_users": 0, "legacy_unlinked_debits": 0},
            orders={"active": 0, "legacy_unverified": 1},
            objects={"public_user_assets": 2, "shared_public_assets": 4, "unknown_role": 0},
            conflict_group_hmacs={"duplicate_email": ["a" * 64]},
            url_inventory_hmac_sha256="b" * 64,
            read_only_proof=_safe_read_only_proof(),
        )
        payload = report.model_dump(mode="json")
        serialized_values = "\n".join(_flatten_strings(payload)).lower()
        for raw_secret in (
            "person@example.com",
            "retired_provider_subject",
            "https://bucket.example/private/photo.jpg",
            "users/123/photo.jpg",
            "secret-token",
        ):
            self.assertNotIn(raw_secret, serialized_values)

    def test_inventory_classifies_non_supabase_providers_without_product_specific_legacy_branches(self) -> None:
        service = _service()
        source = (ROOT / "backend/app/services/production_inventory_service.py").read_text(encoding="utf-8").lower()
        sql = service.build_user_inventory_sql(service.FULL_SCHEMA_SHAPE).lower()
        for retired_product_token in ("wechat", "weixin", "mini_program", "wx_%"):
            self.assertNotIn(retired_product_token, source)
        self.assertIn("other_retired_provider", sql)
        self.assertIn("auth_provider <> 'supabase'", sql)

    def test_report_rejects_extra_sensitive_fields_and_negative_counts(self) -> None:
        service = _service()
        base = {
            "schema": "vowpic.production-inventory.v2",
            "schema_revision": "20260710_0013",
            "source_database_identity_hmac_sha256": "f" * 64,
            "users": {"total": 1},
            "ledger": {},
            "orders": {},
            "objects": {},
            "url_inventory_hmac_sha256": "a" * 64,
            "read_only_proof": _safe_read_only_proof(),
        }
        with self.assertRaises(ValidationError):
            service.ProductionInventoryReport(**base, raw_emails=["person@example.com"])
        with self.assertRaises(ValidationError):
            service.ProductionInventoryReport(**{**base, "users": {"total": -1}})

    def test_report_rejects_unproven_read_only_evidence(self) -> None:
        service = _service()
        with self.assertRaises(ValidationError):
            service.ProductionInventoryReport(
                schema="vowpic.production-inventory.v2",
                schema_revision="20260710_0013",
                source_database_identity_hmac_sha256="f" * 64,
                users={"total": 1},
                ledger={},
                orders={},
                objects={},
                url_inventory_hmac_sha256="a" * 64,
                read_only_proof={
                    "transaction_read_only": False,
                    "default_transaction_read_only": True,
                    "writable_table_count": 0,
                    "write_probe_sqlstate": "25006",
                },
            )

    def test_identifier_hmac_is_keyed_stable_and_non_reversible(self) -> None:
        service = _service()
        first = service.hmac_identifier(b"a" * 32, "email", "Person@Example.com")
        again = service.hmac_identifier(b"a" * 32, "email", "person@example.com")
        other_key = service.hmac_identifier(b"b" * 32, "email", "person@example.com")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other_key)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotIn("person", first)

    def test_write_capable_or_privileged_source_role_is_rejected(self) -> None:
        service = _service()
        safe = _safe_read_only_proof()
        service.validate_read_only_proof(safe)
        for field, value in (
            ("transaction_read_only", False),
            ("default_transaction_read_only", False),
            ("role_superuser", True),
            ("role_create_db", True),
            ("role_bypass_rls", True),
            ("authenticated_role_name", "vowpic_migration_owner"),
            ("role_name", "postgres"),
            ("role_membership_count", 1),
            ("owned_object_count", 1),
            ("readable_inventory_table_count", 11),
            ("readable_inventory_sequence_count", 1),
            ("inventory_select_policy_count", 3),
            ("invalid_inventory_policy_count", 1),
            ("writable_table_count", 1),
            ("write_probe_sqlstate", "42501"),
        ):
            proof = dict(safe)
            proof[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                service.validate_read_only_proof(proof)


class ProductionInventoryBuildTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_sets_read_only_before_any_inventory_query(self) -> None:
        service = _service()
        db = AsyncMock()
        db.scalar.return_value = "20260710_0013"
        proof = _safe_read_only_proof()
        with (
            patch.object(service, "_read_only_proof", new=AsyncMock(return_value=proof)),
            patch.object(
                service,
                "_inventory_schema",
                new=AsyncMock(return_value=service.FULL_SCHEMA_SHAPE),
            ),
            patch.object(service, "_user_inventory", new=AsyncMock(return_value=({"total": 0}, {}))),
            patch.object(service, "_ledger_inventory", new=AsyncMock(return_value={})),
            patch.object(service, "_order_inventory", new=AsyncMock(return_value={})),
            patch.object(service, "_object_inventory", new=AsyncMock(return_value=({}, "0" * 64))),
        ):
            report = await service.build_inventory_report(db, b"k" * 32)

        first_statement = str(db.execute.await_args_list[0].args[0]).upper()
        self.assertIn("SET TRANSACTION READ ONLY", first_statement)
        self.assertEqual(report.schema_revision, "20260710_0013")

    async def test_object_inventory_counts_only_shared_public_assets(self) -> None:
        service = _service()

        class FakeStream:
            def __init__(self, rows):
                self._rows = rows

            def mappings(self):
                return self

            def __aiter__(self):
                self._iterator = iter(self._rows)
                return self

            async def __anext__(self):
                try:
                    return next(self._iterator)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        rows = [
            {
                "owner_id": "legacy-user",
                "source_kind": "order_final",
                "reference": "https://cdn.example.com/shared.jpg",
                "is_asset": True,
                "is_legacy_owner": True,
            },
            {
                "owner_id": "verified-user",
                "source_kind": "order_final",
                "reference": "https://cdn.example.com/shared.jpg",
                "is_asset": True,
                "is_legacy_owner": False,
            },
            {
                "owner_id": "legacy-user",
                "source_kind": "order_source",
                "reference": "https://cdn.example.com/private.jpg?X-Amz-Signature=secret",
                "is_asset": True,
                "is_legacy_owner": True,
            },
            {
                "owner_id": "verified-user",
                "source_kind": "order_source",
                "reference": "https://cdn.example.com/private.jpg?X-Amz-Signature=secret",
                "is_asset": True,
                "is_legacy_owner": False,
            },
            {
                "owner_id": "legacy-user",
                "source_kind": "credit_checkout",
                "reference": "https://checkout.example/session/secret",
                "is_asset": False,
                "is_legacy_owner": True,
            },
        ]
        db = AsyncMock()
        db.stream.return_value = FakeStream(rows)

        counts, checksum = await service._object_inventory(db, b"k" * 32)

        self.assertEqual(counts["public_user_assets"], 2)
        self.assertEqual(counts["private_user_assets"], 2)
        self.assertEqual(counts["shared_public_assets"], 1)
        self.assertEqual(counts["legacy_asset_references"], 2)
        self.assertEqual(counts["legacy_accounts_with_asset_references"], 1)
        self.assertEqual(counts["non_asset_url_references"], 1)
        self.assertRegex(checksum, r"^[0-9a-f]{64}$")


class ProductionInventoryCoverageTest(unittest.TestCase):
    def test_inventory_and_restore_cover_known_url_fields_and_legacy_entitlements(self) -> None:
        service_source = (
            ROOT / "backend" / "app" / "services" / "production_inventory_service.py"
        ).read_text(encoding="utf-8")
        restore_source = (
            ROOT / "backend" / "scripts" / "backup_restore_rehearsal.py"
        ).read_text(encoding="utf-8")

        known_url_fields = (
            "avatar_url",
            "source_image_urls",
            "preview_image_urls",
            "final_image_urls",
            "source_image_url",
            "video_url",
            "checkout_url",
        )
        for field in known_url_fields:
            with self.subTest(field=field):
                self.assertIn(field, service_source)
                self.assertIn(field, restore_source)

        for required_metric in (
            "legacy_accounts_with_orders",
            "legacy_accounts_with_credit_balance",
            "legacy_active_subscriptions",
            "legacy_subscription_credit_grants",
            "upper(orders.status)",
        ):
            with self.subTest(metric=required_metric):
                self.assertIn(required_metric, service_source)

    def test_cli_missing_protected_inputs_is_not_run_without_artifacts(self) -> None:
        script = ROOT / "scripts" / "release" / "inventory_production.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inventory-summary.json"
            env = dict(os.environ)
            env.pop("VOWPIC_TEST_MISSING_DATABASE_URL", None)
            env.pop("VOWPIC_TEST_MISSING_HMAC_KEY", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--database-url-env",
                    "VOWPIC_TEST_MISSING_DATABASE_URL",
                    "--hmac-key-env",
                    "VOWPIC_TEST_MISSING_HMAC_KEY",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 3)
            self.assertIn("NOT_RUN", completed.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(Path(f"{output}.sig").exists())


if __name__ == "__main__":
    unittest.main()
