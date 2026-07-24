"""Task-28 private-media classification, copy, and invalidation contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))


class PrivateMediaMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classifier = importlib.import_module(
            "scripts.release.backfill_media_assets"
        )
        cls.migration = importlib.import_module(
            "scripts.release.migrate_public_media"
        )
        cls.verifier = importlib.import_module(
            "scripts.release.verify_private_media"
        )

    def test_reference_roles_are_explicit_and_unknowns_fail_closed(self) -> None:
        origin = "https://public.example"
        cases = (
            ("order_source", f"{origin}/users/a/source.jpg", "PUBLIC_USER_SOURCE"),
            (
                "order_preview",
                f"{origin}/users/a/preview.png",
                "PUBLIC_USER_CANDIDATE",
            ),
            ("order_final", f"{origin}/users/a/final.webp", "PUBLIC_USER_FINAL"),
            (
                "live_portrait_video",
                f"{origin}/users/a/result.mp4",
                "PUBLIC_USER_FINAL",
            ),
            ("order_final", f"{origin}/marketing/hero.webp", "PUBLIC_PRODUCT_ASSET"),
            ("order_source", f"{origin}/tmp/debug.png", "TEMPORARY_DEBUG"),
            (
                "order_source",
                "https://external.example/a.png",
                "EXTERNAL_REVIEW",
            ),
            (
                "order_source",
                f"{origin}/private/a.png?token=secret",
                "PRIVATE_EXISTING",
            ),
            (
                "user_avatar",
                f"{origin}/users/a/avatar.png",
                "EXTERNAL_REVIEW",
            ),
        )
        for source_kind, reference, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    self.classifier.classify_media_reference(
                        source_kind=source_kind,
                        reference=reference,
                        approved_public_origin=origin,
                    ),
                    expected,
                )

    def test_legacy_object_path_rejects_origin_and_path_ambiguity(self) -> None:
        origin = "https://public.example"
        self.assertEqual(
            self.migration._reference_path(
                f"{origin}/users/a/final-1.webp",
                approved_origin=origin,
            ),
            "users/a/final-1.webp",
        )
        invalid = (
            "http://public.example/users/a.jpg",
            "https://other.example/users/a.jpg",
            f"{origin}/users/a.jpg?token=secret",
            f"{origin}/users/a.jpg#fragment",
            f"{origin}/users/%2e%2e/a.jpg",
            f"{origin}/users/../a.jpg",
            f"{origin}/users//a.jpg",
            f"{origin}/用户/a.jpg",
        )
        for reference in invalid:
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.migration._reference_path(
                        reference,
                        approved_origin=origin,
                    )

    def test_mime_detection_is_byte_based_and_fail_closed(self) -> None:
        self.assertEqual(
            self.migration._mime_type(b"\xff\xd8\xffrest", "order_source"),
            "image/jpeg",
        )
        self.assertEqual(
            self.migration._mime_type(
                b"\x89PNG\r\n\x1a\nrest",
                "order_source",
            ),
            "image/png",
        )
        with self.assertRaises(ValueError):
            self.migration._mime_type(b"not-an-image", "order_source")

    def test_same_legacy_url_in_two_orders_gets_distinct_private_facts(
        self,
    ) -> None:
        reference = "https://public.example/users/a/shared.webp"
        first = self.migration.legacy_media_asset_id(
            source_kind="order_final",
            source_row_id="11111111-1111-4111-8111-111111111111",
            reference=reference,
        )
        second = self.migration.legacy_media_asset_id(
            source_kind="order_final",
            source_row_id="22222222-2222-4222-8222-222222222222",
            reference=reference,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(
            first,
            self.migration.legacy_media_asset_id(
                source_kind="order_final",
                source_row_id="11111111-1111-4111-8111-111111111111",
                reference=reference,
            ),
        )

    def test_copy_readback_precedes_fact_switch_and_old_reference_is_retained(
        self,
    ) -> None:
        source = (
            ROOT / "scripts/release/migrate_public_media.py"
        ).read_text(encoding="utf-8")
        copy_start = source.index("async def _copy_batch")
        delete_start = source.index("async def _delete_batch")
        copy_source = source[copy_start:delete_start]
        self.assertLess(
            copy_source.index("private Blob copy read-back checksum mismatch"),
            copy_source.index("await _link_asset"),
        )
        self.assertNotIn("source_image_urls =", copy_source)
        self.assertNotIn("preview_image_urls =", copy_source)
        self.assertNotIn("final_image_urls =", copy_source)

    def test_private_verdict_reconciles_counts_and_runtime_bundle(self) -> None:
        inventory = {
            "schema": "vowpic.production-inventory.v2",
            "url_inventory_hmac_sha256": "a" * 64,
        }
        migration = {
            "tool": "migrate_public_media",
            "passed": True,
            "mode": "copy-write",
            "counts": {"eligible": 2, "copied": 1, "already_copied": 1},
            "blockers": {},
            "inventory_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "runtime_bundle_id": "rtb_" + "d" * 64,
        }
        verdict = self.verifier.build_private_media_verdict(
            inventory=inventory,
            migration_report=migration,
            live_probe={
                "ready": True,
                "deployment_id": "dpl_target",
                "runtime_bundle_id": migration["runtime_bundle_id"],
            },
        )
        self.assertTrue(verdict["passed"])
        with self.assertRaisesRegex(ValueError, "runtime bundle drift"):
            self.verifier.build_private_media_verdict(
                inventory=inventory,
                migration_report=migration,
                live_probe={
                    "ready": True,
                    "deployment_id": "dpl_target",
                    "runtime_bundle_id": "rtb_" + "e" * 64,
                },
            )

    def test_delete_requires_two_complete_independent_probe_sets(self) -> None:
        now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        inventory = {
            "schema": "vowpic.production-inventory.v2",
            "url_inventory_hmac_sha256": "a" * 64,
        }
        migration = {
            "tool": "migrate_public_media",
            "passed": True,
            "mode": "delete-write",
            "counts": {"eligible": 1, "deleted": 1, "already_deleted": 0},
            "blockers": {},
            "inventory_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "runtime_bundle_id": "rtb_" + "d" * 64,
        }

        def probe(location: str):
            return {
                "schema": "vowpic.legacy-url-invalidation.v1",
                "passed": True,
                "failed_count": 0,
                "expected_count": 1,
                "probed_count": 1,
                "location_id": location,
                "verified_at": now.isoformat(),
                "requests_per_url": 1,
                "inventory_sha256": migration["inventory_sha256"],
                "manifest_sha256": migration["manifest_sha256"],
                "results": [
                    {
                        "url_hmac_sha256": "e" * 64,
                        "attempts": [
                            {
                                "status": 404,
                                "byte_count": 0,
                                "differs_from_old_checksum": True,
                                "passed": True,
                            }
                        ],
                        "passed": True,
                    }
                ],
            }

        with self.assertRaisesRegex(ValueError, "exactly two"):
            self.verifier.build_private_media_verdict(
                inventory=inventory,
                migration_report=migration,
                probe_reports=[probe("production")],
                now=now,
            )
        verdict = self.verifier.build_private_media_verdict(
            inventory=inventory,
            migration_report=migration,
            probe_reports=[probe("production"), probe("external")],
            now=now,
        )
        self.assertEqual(
            verdict["probe_locations"],
            ["external", "production"],
        )

    def test_delete_write_requires_precommitted_dry_run_digest(self) -> None:
        report = {
            "tool": "migrate_public_media",
            "mode": "delete-dry",
            "passed": True,
            "write_performed": False,
            "inventory_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "runtime_bundle_id": "rtb_" + "c" * 64,
            "source_revision": "20260710_0020",
            "blockers": {},
            "details": {"operation": "delete-old-public"},
        }
        raw = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        args = SimpleNamespace(
            required_dry_run_report="bound-report.json",
            expected_dry_run_sha256=hashlib.sha256(raw).hexdigest(),
        )
        invocation = SimpleNamespace(
            inventory_sha256=report["inventory_sha256"],
            manifest_sha256=report["manifest_sha256"],
            contract=SimpleNamespace(
                runtime_bundle_id=report["runtime_bundle_id"],
                source_revision=report["source_revision"],
            ),
        )
        with patch.object(Path, "read_bytes", return_value=raw):
            self.migration._verify_required_dry_run(args, invocation)
            args.expected_dry_run_sha256 = "d" * 64
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.migration._verify_required_dry_run(args, invocation)

    def test_node_probe_does_not_follow_redirects_or_emit_raw_urls(self) -> None:
        source = (
            ROOT / "scripts/release/verify_legacy_url_invalidation.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('redirect: "manual"', source)
        result_start = source.index("return {\n    url_hmac_sha256")
        result_end = source.index("\n  };", result_start)
        self.assertNotIn("url:", source[result_start:result_end])
        self.assertIn("await unlink(manifestPath)", source)

if __name__ == "__main__":
    unittest.main()
