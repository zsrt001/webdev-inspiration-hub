"""Authenticated streaming upload and private-object contract tests."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from PIL import Image
from pydantic import ValidationError

from app.core.config import Settings
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.upload_batch import UploadBatch, UploadBatchStatus
from app.models.upload_quota_reservation import (
    UploadQuotaReservation,
    UploadQuotaReservationStatus,
)
from app.models.upload_quota_state import UploadQuotaState
from app.models.upload_quota_window import UploadQuotaWindow, UploadQuotaWindowKind
from app.services import media_asset_service
from app.services.media_asset_service import (
    UploadBatchError,
    UploadValidationError,
    deterministic_object_key,
    issue_provider_grant_token,
    store_validated_batch,
    validate_and_reencode_image,
)
from app.services.storage import DeleteResult, StorageService
from app.services.upload_quota_service import UploadQuotaExceeded
from app.services import upload_quota_service


def _image_bytes(
    image_format: str = "JPEG",
    *,
    size: tuple[int, int] = (640, 960),
    exif: bool = False,
) -> bytes:
    image = Image.new("RGB", size, (180, 140, 120))
    output = BytesIO()
    kwargs: dict[str, object] = {}
    if exif:
        metadata = Image.Exif()
        metadata[0x010E] = "private source description"
        kwargs["exif"] = metadata
    image.save(output, format=image_format, **kwargs)
    return output.getvalue()


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.flushes = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class _FailingStore:
    def __init__(self, *, fail_on_put: int | None = None) -> None:
        self.fail_on_put = fail_on_put
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def put_private(self, object_key: str, data: bytes, content_type: str) -> None:
        self.puts.append(object_key)
        if self.fail_on_put == len(self.puts):
            raise RuntimeError("simulated private store failure")

    def read_private(self, object_key: str) -> bytes:
        raise AssertionError("not used")

    def delete_private(self, object_key: str) -> DeleteResult:
        self.deletes.append(object_key)
        return DeleteResult.DELETED


class _CountingRequest:
    def __init__(self) -> None:
        self.headers = {"content-type": "multipart/form-data; boundary=test-boundary"}
        self.state = SimpleNamespace(request_id="request-1")
        self.stream_reads = 0

    async def stream(self):
        self.stream_reads += 1
        yield b"--test-boundary--\r\n"


class _QueuedResult:
    def __init__(self, *, one: object | None = None, many: list[object] | None = None) -> None:
        self.one = one
        self.many = many or []

    def scalar_one(self):
        if self.one is None:
            raise AssertionError("queued scalar result is missing")
        return self.one

    def scalars(self):
        return self

    def all(self) -> list[object]:
        return list(self.many)


class _QueuedDb:
    def __init__(self, results: list[_QueuedResult]) -> None:
        self.results = list(results)
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class AuthenticatedUploadTest(unittest.IsolatedAsyncioTestCase):
    def test_upload_security_limits_cannot_be_raised_by_environment(self) -> None:
        unsafe_values = {
            "upload_max_bytes": 10_485_761,
            "upload_max_files": 6,
            "upload_max_pixels": 40_000_001,
            "upload_requests_per_hour": 21,
            "upload_bytes_per_day": 209_715_201,
            "upload_max_concurrent": 3,
            "provider_asset_grant_ttl_seconds": 601,
            "provider_asset_grant_max_reads": 4,
            "external_fetch_max_redirects": 3,
            "external_fetch_connect_timeout_seconds": 6,
            "external_fetch_total_timeout_seconds": 31,
            "external_fetch_max_bytes": 10_485_761,
        }
        for field_name, unsafe_value in unsafe_values.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    Settings(**{field_name: unsafe_value})

    def test_safe_decode_validates_magic_and_strips_exif(self) -> None:
        source = _image_bytes("JPEG", exif=True)
        validated = validate_and_reencode_image(
            source,
            declared_content_type="image/jpeg",
        )

        self.assertEqual(validated.mime_type, "image/jpeg")
        self.assertEqual((validated.width, validated.height), (640, 960))
        self.assertEqual(validated.byte_size, len(validated.content))
        with Image.open(BytesIO(validated.content)) as decoded:
            decoded.load()
            self.assertEqual(decoded.format, "JPEG")
            self.assertEqual(len(decoded.getexif()), 0)
            self.assertNotIn("icc_profile", decoded.info)

    def test_declared_mime_magic_mismatch_and_truncated_image_are_rejected(self) -> None:
        with self.assertRaises(UploadValidationError) as mismatch:
            validate_and_reencode_image(
                _image_bytes("PNG"),
                declared_content_type="image/jpeg",
            )
        self.assertEqual(mismatch.exception.code, "upload_type_mismatch")

        truncated = _image_bytes("PNG")[:-12]
        with self.assertRaises(UploadValidationError) as invalid:
            validate_and_reencode_image(
                truncated,
                declared_content_type="image/png",
            )
        self.assertEqual(invalid.exception.code, "upload_decode_failed")

    def test_pixel_limit_is_enforced_after_real_header_decode(self) -> None:
        with patch.object(media_asset_service, "UPLOAD_MAX_PIXELS", 100):
            with self.assertRaises(UploadValidationError) as raised:
                validate_and_reencode_image(
                    _image_bytes("PNG", size=(11, 10)),
                    declared_content_type="image/png",
                )
        self.assertEqual(raised.exception.code, "upload_pixel_limit")

    def test_object_key_and_grant_token_are_server_derived(self) -> None:
        owner_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        key = deterministic_object_key(
            owner_user_id=owner_id,
            batch_id=batch_id,
            part_ordinal=2,
            asset_id=asset_id,
            mime_type="image/jpeg",
        )
        self.assertEqual(
            key,
            f"users/{owner_id}/uploads/{batch_id}/0002-{asset_id}.jpg",
        )
        self.assertNotIn("wedding.jpg", key)

        raw, token_hash = issue_provider_grant_token()
        self.assertGreaterEqual(len(raw), 43)
        self.assertEqual(len(token_hash), 64)
        self.assertNotEqual(raw, token_hash)

    async def test_storage_failure_rolls_back_every_prepared_asset(self) -> None:
        owner_id = uuid.uuid4()
        batch = UploadBatch(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            status=UploadBatchStatus.PENDING_UPLOAD,
            request_id="request-1",
            expected_files=2,
            received_files=0,
        )
        images = [
            validate_and_reencode_image(_image_bytes(), declared_content_type="image/jpeg"),
            validate_and_reencode_image(_image_bytes(), declared_content_type="image/jpeg"),
        ]
        db = _FakeDb()
        store = _FailingStore(fail_on_put=2)

        with self.assertRaises(UploadBatchError):
            await store_validated_batch(
                db,
                batch=batch,
                owner_user_id=owner_id,
                validated_images=images,
                object_store=store,
            )

        assets = [value for value in db.added if hasattr(value, "object_key")]
        self.assertEqual(len(assets), 2)
        self.assertTrue(all(asset.status != MediaAssetStatus.ACTIVE for asset in assets))
        self.assertEqual(batch.status, UploadBatchStatus.UPLOAD_FAILED)
        self.assertEqual(store.deletes, store.puts)

    async def test_derivative_failure_attempts_private_object_cleanup(self) -> None:
        now = datetime.now(timezone.utc)
        owner_id = uuid.uuid4()
        parent = MediaAsset(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            role=MediaAssetRole.SOURCE,
            storage_provider="local",
            object_key="users/source.jpg",
            sha256="a" * 64,
            mime_type="image/jpeg",
            byte_size=10,
            width=10,
            height=10,
            access_level="private",
            policy_version="test-v1",
            expires_at=now + timedelta(days=1),
            status=MediaAssetStatus.ACTIVE,
        )
        validated = validate_and_reencode_image(
            _image_bytes(size=(100, 100)),
            declared_content_type="image/jpeg",
        )
        db = _FakeDb()
        store = _FailingStore(fail_on_put=1)

        with self.assertRaises(UploadBatchError):
            await media_asset_service.store_private_derivative(
                db,
                owner_user_id=owner_id,
                parent_asset=parent,
                role=MediaAssetRole.INTERMEDIATE,
                validated=validated,
                object_store=store,
                now=now,
            )

        derivative = db.added[0]
        self.assertEqual(store.deletes, [derivative.object_key])
        self.assertEqual(derivative.status, MediaAssetStatus.DELETED)
        self.assertEqual(derivative.deleted_at, now)

    async def test_provider_grant_is_hash_only_bounded_and_runtime_stamped(self) -> None:
        now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        owner_id = uuid.uuid4()
        asset = MediaAsset(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            role=MediaAssetRole.SOURCE,
            storage_provider="s3",
            object_key="users/source.jpg",
            sha256="b" * 64,
            mime_type="image/jpeg",
            byte_size=10,
            width=10,
            height=10,
            access_level="private",
            policy_version="test-v1",
            expires_at=now + timedelta(hours=1),
            status=MediaAssetStatus.ACTIVE,
        )
        db = _FakeDb()

        issued = await media_asset_service.create_provider_grant(
            db,
            asset=asset,
            provider="wenwen",
            purpose="gatekeeper",
            now=now,
        )

        self.assertEqual(issued.grant.max_reads, 3)
        self.assertEqual(issued.grant.expires_at, now + timedelta(seconds=600))
        self.assertEqual(
            issued.grant.token_hash,
            __import__("hashlib").sha256(issued.token.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(issued.token, repr(issued.grant.__dict__))

        with (
            patch.object(media_asset_service.settings, "runtime_environment", "production"),
            patch.object(media_asset_service.settings, "runtime_bundle_id", ""),
            patch.object(media_asset_service.settings, "vercel_deployment_id", ""),
            patch.object(media_asset_service.settings, "release_role", ""),
        ):
            with self.assertRaises(media_asset_service.AssetAccessError) as invalid_runtime:
                await media_asset_service.create_provider_grant(
                    _FakeDb(),
                    asset=asset,
                    provider="wenwen",
                    purpose="gatekeeper",
                    now=now,
                )
        self.assertEqual(invalid_runtime.exception.code, "runtime_identity_invalid")

    async def test_slot_exhaustion_reads_zero_request_body_bytes(self) -> None:
        request = _CountingRequest()
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeDb()
        with patch.object(
            media_asset_service.upload_quota_service,
            "create_upload_batch",
            AsyncMock(side_effect=UploadQuotaExceeded("upload_concurrent_limit")),
        ):
            with self.assertRaises(UploadQuotaExceeded):
                await media_asset_service.stream_authenticated_multipart_upload(
                    request,
                    user,
                    db,
                )
        self.assertEqual(request.stream_reads, 0)

    async def test_stale_batch_settles_attempted_bytes_and_releases_slot_once(self) -> None:
        now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        user_id = uuid.uuid4()
        batch = UploadBatch(
            id=uuid.uuid4(),
            owner_user_id=user_id,
            status=UploadBatchStatus.PENDING_UPLOAD,
            request_id="stale-request",
            expected_files=1,
            received_files=0,
            expires_at=now - timedelta(minutes=1),
            lease_expires_at=now - timedelta(seconds=1),
        )
        window = UploadQuotaWindow(
            id=uuid.uuid4(),
            user_id=user_id,
            window_kind=UploadQuotaWindowKind.DAILY_BYTES,
            window_start=now.replace(hour=0),
            request_count=0,
            attempted_bytes=100,
            reserved_bytes=10_485_760,
        )
        reservation = UploadQuotaReservation(
            id=uuid.uuid4(),
            batch_id=batch.id,
            quota_window_id=window.id,
            part_ordinal=0,
            reserved_bytes=10_485_760,
            actual_attempted_bytes=1_234_567,
            status=UploadQuotaReservationStatus.RESERVED,
        )
        state = UploadQuotaState(user_id=user_id, active_slots=1, version=4)
        db = _QueuedDb(
            [
                _QueuedResult(many=[batch]),
                _QueuedResult(many=[reservation]),
                _QueuedResult(one=window),
                _QueuedResult(one=state),
            ]
        )

        recovered = await upload_quota_service.recover_stale_upload_batches(
            db,
            now=now,
            limit=10,
        )

        self.assertEqual(recovered, 1)
        self.assertEqual(window.reserved_bytes, 0)
        self.assertEqual(window.attempted_bytes, 1_234_667)
        self.assertEqual(reservation.status, UploadQuotaReservationStatus.RELEASED)
        self.assertEqual(reservation.settled_at, now)
        self.assertEqual(reservation.slot_released_at, now)
        self.assertEqual(state.active_slots, 0)
        self.assertEqual(state.version, 5)
        self.assertEqual(batch.status, UploadBatchStatus.UPLOAD_FAILED)
        self.assertEqual(batch.failure_code, "upload_intent_expired")
        self.assertEqual(batch.slot_released_at, now)
        self.assertEqual(db.commits, 1)

        recovered_again = await upload_quota_service.recover_stale_upload_batches(
            _QueuedDb([_QueuedResult(many=[])]),
            now=now,
        )
        self.assertEqual(recovered_again, 0)

    def test_public_upload_primitives_are_absent(self) -> None:
        storage_source = inspect.getsource(StorageService).lower()
        self.assertNotIn("public-read", storage_source)
        self.assertNotIn('access="public"', storage_source)
        self.assertTrue(hasattr(StorageService, "put_private"))
        self.assertTrue(hasattr(StorageService, "read_private"))
        self.assertTrue(hasattr(StorageService, "delete_private"))

        from app.routers import media

        route_source = inspect.getsource(media.upload_media)
        self.assertNotIn("UploadFile", route_source)
        self.assertNotIn("File(", route_source)
        self.assertNotIn("Form(", route_source)


if __name__ == "__main__":
    unittest.main()
