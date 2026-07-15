"""Private master and fixed delivery artifact contracts."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import unittest
import uuid

from PIL import Image

from app.models.media_asset import MediaAssetRole
from app.services.delivery_asset_service import (
    DeliveryArtifactPayload,
    DeliverySettlementError,
    _delete_untracked_delivery_objects,
    _store_one_artifact,
    build_delivery_assets,
)
from app.services.job_lease_service import JobLease, StaleWorkerFence
from app.services import postprocess_service
from app.services.postprocess_service import (
    PAID_VARIANT_RATIOS,
    ValidatedPrivateImage,
    render_private_delivery_set,
)
from app.services.storage import DeleteResult


def _candidate() -> ValidatedPrivateImage:
    image = Image.new("RGB", (900, 1200), (82, 111, 138))
    output = BytesIO()
    image.save(output, format="JPEG", quality=94)
    payload = output.getvalue()
    return ValidatedPrivateImage(
        asset_id=uuid.uuid4(),
        image_bytes=payload,
        mime_type="image/jpeg",
        sha256=sha256(payload).hexdigest(),
    )


def _delivery_context(*, trial: bool = False):
    candidate = _candidate()
    return SimpleNamespace(
        job=SimpleNamespace(id=uuid.uuid4()),
        attempt=SimpleNamespace(id=uuid.uuid4()),
        order=SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            template_id="solo_royal_castle",
            reservation_id=uuid.uuid4(),
            funding_policy_snapshot={
                "policy_version": "order-funding.v1",
                "is_trial": trial,
                "allowed_lot_class": "WELCOME_ONLY" if trial else "PAID_ONLY",
            },
        ),
        candidate=SimpleNamespace(
            id=candidate.asset_id,
            object_key="users/test/candidate.jpg",
            image_bytes=candidate.image_bytes,
            mime_type=candidate.mime_type,
            sha256=candidate.sha256,
            byte_size=len(candidate.image_bytes),
        ),
        verdict=SimpleNamespace(id=uuid.uuid4()),
    )


class DeliveryAssetsTest(unittest.TestCase):
    def test_renderer_returns_private_master_and_all_six_exact_ratios(self) -> None:
        with (
            patch.object(postprocess_service.settings, "postprocess_max_long_edge", 1200),
            patch.object(postprocess_service.settings, "postprocess_upscale_factor", 1),
        ):
            rendered = render_private_delivery_set(_candidate())

        self.assertEqual(rendered.master.role, MediaAssetRole.FINAL_MASTER)
        self.assertEqual(set(rendered.variants), set(PAID_VARIANT_RATIOS))
        self.assertFalse(hasattr(rendered.master, "url"))
        self.assertFalse(hasattr(rendered.master, "object_key"))
        for ratio_name, artifact in rendered.variants.items():
            with self.subTest(ratio=ratio_name), Image.open(
                BytesIO(artifact.image_bytes)
            ) as decoded:
                width_ratio, height_ratio = PAID_VARIANT_RATIOS[ratio_name]
                self.assertEqual(decoded.width * height_ratio, decoded.height * width_ratio)
                self.assertEqual(artifact.role, MediaAssetRole.DELIVERY_VARIANT)
                self.assertEqual(artifact.sha256, sha256(artifact.image_bytes).hexdigest())

    def test_incomplete_variant_set_is_never_complete(self) -> None:
        with (
            patch.object(postprocess_service.settings, "postprocess_max_long_edge", 1200),
            patch.object(postprocess_service.settings, "postprocess_upscale_factor", 1),
        ):
            rendered = render_private_delivery_set(_candidate())

        rendered.variants.pop("9:16")
        self.assertFalse(rendered.is_complete)


class DeliveryFenceBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_read_and_render_happen_only_after_releasing_row_locks(self) -> None:
        context = _delivery_context()
        events: list[str] = []

        async def commit() -> None:
            events.append("commit")

        def read_private(_object_key: str) -> bytes:
            events.append("read")
            return context.candidate.image_bytes

        class StopAfterRender(RuntimeError):
            pass

        def render(*_args, **_kwargs):
            events.append("render")
            raise StopAfterRender

        db = SimpleNamespace(commit=AsyncMock(side_effect=commit))
        store = SimpleNamespace(read_private=read_private)
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        load_context = AsyncMock(return_value=context)
        with (
            patch(
                "app.services.delivery_asset_service._load_delivery_context",
                load_context,
            ),
            patch(
                "app.services.delivery_asset_service.render_private_delivery_set",
                side_effect=render,
            ),
        ):
            with self.assertRaises(StopAfterRender):
                await build_delivery_assets(
                    db,
                    attempt_id=context.attempt.id,
                    lease=lease,
                    object_store=store,
                )

        self.assertEqual(events, ["commit", "read", "commit", "render"])
        self.assertEqual(load_context.await_count, 2)

    async def test_stale_fence_after_render_never_reaches_funding_or_activation(self) -> None:
        context = _delivery_context()
        candidate = context.candidate
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        stale = StaleWorkerFence("stale", context.job.id)
        renderer = SimpleNamespace(
            master=SimpleNamespace(
                name="master_3x4",
                image_bytes=candidate.image_bytes,
                mime_type=candidate.mime_type,
                sha256=candidate.sha256,
            )
        )
        with (
            patch(
                "app.services.delivery_asset_service._load_delivery_context",
                AsyncMock(side_effect=(context, context, stale)),
            ),
            patch(
                "app.services.delivery_asset_service.render_private_delivery_set",
                return_value=renderer,
            ),
            patch(
                "app.services.delivery_asset_service._lock_reservation_funding",
                AsyncMock(side_effect=AssertionError("funding reached under a stale fence")),
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await build_delivery_assets(
                    SimpleNamespace(commit=AsyncMock()),
                    attempt_id=context.attempt.id,
                    lease=lease,
                    object_store=SimpleNamespace(
                        read_private=lambda _key: candidate.image_bytes
                    ),
                )

    async def test_stale_fence_after_storage_write_cannot_continue_delivery(self) -> None:
        context = _delivery_context()
        candidate = context.candidate
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        payload = DeliveryArtifactPayload(
            asset=SimpleNamespace(
                object_key="users/test/delivery/master.jpg",
                mime_type="image/jpeg",
                byte_size=len(candidate.image_bytes),
                sha256=candidate.sha256,
                width=900,
                height=1200,
            ),
            image_bytes=candidate.image_bytes,
        )
        store = SimpleNamespace(
            put_private=Mock(),
            read_private=Mock(return_value=candidate.image_bytes),
        )
        with patch(
            "app.services.delivery_asset_service._load_delivery_context",
            AsyncMock(
                side_effect=(
                    context,
                    StaleWorkerFence("stale", context.job.id),
                )
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await _store_one_artifact(
                    SimpleNamespace(commit=AsyncMock()),
                    payload=payload,
                    attempt_id=context.attempt.id,
                    lease=lease,
                    object_store=store,
                    now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                )

        store.put_private.assert_called_once()
        store.read_private.assert_called_once()

    async def test_orphan_recovery_deletes_only_untracked_keys_inside_exact_prefix(self) -> None:
        context = _delivery_context()
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        prefix = (
            f"users/{context.order.user_id}/generation/{context.job.id}/delivery/"
        )
        expected_key = f"{prefix}expected.jpg"
        orphan_key = f"{prefix}crash-left.jpg"
        payload = DeliveryArtifactPayload(
            asset=SimpleNamespace(object_key=expected_key),
            image_bytes=b"expected",
        )
        store = SimpleNamespace(
            list_private=Mock(
                return_value=(expected_key, orphan_key)
            ),
            delete_private=Mock(return_value=DeleteResult.DELETED),
        )
        with patch(
            "app.services.delivery_asset_service._load_delivery_context",
            AsyncMock(return_value=context),
        ):
            await _delete_untracked_delivery_objects(
                SimpleNamespace(commit=AsyncMock()),
                context=context,
                payloads=(payload,),
                attempt_id=context.attempt.id,
                lease=lease,
                object_store=store,
                now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            )

        store.delete_private.assert_called_once_with(orphan_key)

    async def test_orphan_recovery_rejects_listing_that_escapes_job_prefix(self) -> None:
        context = _delivery_context()
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        store = SimpleNamespace(
            list_private=Mock(
                return_value=("users/another-user/private.jpg",)
            ),
            delete_private=Mock(),
        )
        with patch(
            "app.services.delivery_asset_service._load_delivery_context",
            AsyncMock(return_value=context),
        ):
            with self.assertRaisesRegex(
                DeliverySettlementError,
                "delivery_object_listing_escaped_prefix",
            ):
                await _delete_untracked_delivery_objects(
                    SimpleNamespace(commit=AsyncMock()),
                    context=context,
                    payloads=(),
                    attempt_id=context.attempt.id,
                    lease=lease,
                    object_store=store,
                    now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                )

        store.delete_private.assert_not_called()

    async def test_stale_fence_after_trial_watermark_never_reaches_funding(self) -> None:
        context = _delivery_context(trial=True)
        candidate = context.candidate
        lease = JobLease(
            context.job.id,
            "worker-a",
            uuid.uuid4(),
            1,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 13, 12, 2, tzinfo=timezone.utc),
        )
        stale = StaleWorkerFence("stale", context.job.id)
        renderer = SimpleNamespace(
            master=SimpleNamespace(
                name="master_3x4",
                image_bytes=candidate.image_bytes,
                mime_type=candidate.mime_type,
                sha256=candidate.sha256,
            )
        )
        with (
            patch(
                "app.services.delivery_asset_service._load_delivery_context",
                AsyncMock(side_effect=(context, context, context, stale)),
            ),
            patch(
                "app.services.delivery_asset_service.render_private_delivery_set",
                return_value=renderer,
            ),
            patch(
                "app.services.delivery_asset_service.build_trial_watermark_bytes",
                return_value=SimpleNamespace(),
            ),
            patch(
                "app.services.delivery_asset_service._lock_reservation_funding",
                AsyncMock(side_effect=AssertionError("funding reached under a stale fence")),
            ),
        ):
            with self.assertRaises(StaleWorkerFence):
                await build_delivery_assets(
                    SimpleNamespace(commit=AsyncMock()),
                    attempt_id=context.attempt.id,
                    lease=lease,
                    object_store=SimpleNamespace(
                        read_private=lambda _key: candidate.image_bytes
                    ),
                )


if __name__ == "__main__":
    unittest.main()
