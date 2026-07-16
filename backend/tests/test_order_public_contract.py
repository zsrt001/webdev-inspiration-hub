"""Customer order DTOs expose private asset identities, never storage URLs."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid

from app.models.order import Order, OrderStatus
from app.schemas.order import OrderAssetRead, OrderRead


class OrderPublicContractTest(unittest.TestCase):
    def test_internal_settlement_states_project_to_the_strict_public_contract(self) -> None:
        now = datetime.now(timezone.utc)
        for internal, expected in (
            ("UNSETTLED", "NOT_CHARGED"),
            ("RESERVED", "NOT_CHARGED"),
            ("RELEASED", "NOT_CHARGED"),
            ("CAPTURED", "CAPTURED"),
            ("REFUNDED", "REFUNDED"),
            ("RECONCILING", "RECONCILING"),
        ):
            with self.subTest(internal=internal):
                projected = OrderRead.model_validate(
                    {
                        "id": uuid.uuid4(),
                        "user_id": uuid.uuid4(),
                        "status": OrderStatus.QUEUED,
                        "settlement_status": internal,
                        "delivery_status": "PENDING",
                        "price_cents": 0,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                self.assertEqual(projected.settlement_status, expected)

        with self.assertRaisesRegex(ValueError, "unsupported order settlement status"):
            OrderRead.model_validate(
                {
                    "id": uuid.uuid4(),
                    "user_id": uuid.uuid4(),
                    "status": OrderStatus.QUEUED,
                    "settlement_status": "SILENT_DEFAULT",
                    "delivery_status": "PENDING",
                    "price_cents": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    def test_order_read_has_no_legacy_or_provider_url_surface(self) -> None:
        now = datetime.now(timezone.utc)
        order = Order(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            status=OrderStatus.READY,
            template_id="solo_royal_castle",
            source_image_urls={"images": ["https://cdn.invalid/source.jpg"]},
            preview_image_urls={"image_1": "https://cdn.invalid/preview.jpg"},
            final_image_urls={"image_1": "https://cdn.invalid/final.jpg"},
            generation_params={"provider_task_id": "provider-secret"},
            settlement_status="CAPTURED",
            delivery_status="READY",
            price_cents=0,
            created_at=now,
            updated_at=now,
        )

        dumped = OrderRead.model_validate(order).model_dump(mode="json")

        for forbidden in (
            "source_image_urls",
            "preview_image_urls",
            "final_image_urls",
            "preview_master_image_url",
            "final_master_image_url",
            "download_variants",
            "generation_params",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertNotIn("https://", str(dumped))
        self.assertNotIn("provider-secret", str(dumped))
        self.assertEqual(dumped["assets"], [])

    def test_asset_dto_contains_only_authorized_stream_identity(self) -> None:
        order_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        asset = OrderAssetRead(
            id=asset_id,
            role="delivery_variant",
            status="ACTIVE",
            width=900,
            height=1200,
            download_path=f"/api/v1/orders/{order_id}/assets/{asset_id}/download",
        )

        dumped = asset.model_dump(mode="json")
        self.assertEqual(dumped["id"], str(asset_id))
        self.assertNotIn("object_key", dumped)
        self.assertNotIn("url", dumped)
        self.assertFalse(any("http" in str(value) for value in dumped.values()))


if __name__ == "__main__":
    unittest.main()
