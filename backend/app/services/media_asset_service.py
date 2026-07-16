"""Private media validation, upload orchestration, ownership, and provider grants."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import re
import secrets
from typing import Any
import uuid
import warnings
from urllib.parse import urlsplit

from python_multipart.multipart import parse_options_header
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.asset_access_grant import AssetAccessGrant
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.upload_batch import UploadBatch, UploadBatchStatus
from app.models.user import User
from app.services import upload_quota_service
from app.services.media_deletion_service import (
    DeletionRequestResult,
    request_asset_deletion,
)
from app.services.storage import DeleteResult, PrivateObjectStore, storage_service


settings = get_settings()
UPLOAD_MAX_BYTES = 10_485_760
UPLOAD_MAX_FILES = 5
UPLOAD_MAX_PIXELS = 40_000_000
_HEADER_LIMIT = 8_192
_HEADER_LINE_LIMIT = 1_024
_BOUNDARY_LIMIT = 70
_PROGRESS_STEP = 1_048_576
_ALLOWED_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_BOUNDARY_PATTERN = re.compile(rb"^[0-9A-Za-z'()+_,./:=?-]{1,70}$")


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _normalized_origin_authority(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise AssetAccessError("asset_grant_origin_invalid") from exc
    host = str(parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise AssetAccessError("asset_grant_origin_invalid")
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return parsed.scheme, authority


def validate_provider_grant_origin(*, host_header: str, request_scheme: str) -> None:
    """Reject alternate Host routing before a path token reaches storage."""

    raw_host = str(host_header or "").strip().lower()
    raw_scheme = str(request_scheme or "").strip().lower()
    if (
        not raw_host
        or any(character in raw_host for character in (",", " ", "\t", "@", "/"))
        or raw_scheme not in {"http", "https"}
    ):
        raise AssetAccessError("asset_grant_origin_invalid")
    expected_scheme, expected_authority = _normalized_origin_authority(
        settings.effective_provider_grant_origin
    )
    actual_scheme, actual_authority = _normalized_origin_authority(
        f"{raw_scheme}://{raw_host}"
    )
    if (
        actual_authority != expected_authority
        or actual_scheme != expected_scheme
        or (settings.runtime_environment != "development" and actual_scheme != "https")
    ):
        raise AssetAccessError("asset_grant_origin_invalid")


class UploadBatchError(RuntimeError):
    def __init__(self, code: str = "upload_batch_rejected") -> None:
        super().__init__(code)
        self.code = code


class AssetAccessError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ValidatedImageBytes:
    content: bytes
    mime_type: str
    width: int
    height: int
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class PrivateAssetBytes:
    asset: MediaAsset
    content: bytes
    mime_type: str


@dataclass(frozen=True)
class IssuedAssetGrant:
    grant: AssetAccessGrant
    token: str
    read_url: str


def _magic_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _canonical_rgb(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image)
    if normalized.mode in {"RGBA", "LA"} or (
        normalized.mode == "P" and "transparency" in normalized.info
    ):
        rgba = normalized.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return normalized.convert("RGB")


def validate_and_reencode_image(
    raw: bytes,
    *,
    declared_content_type: str | None,
) -> ValidatedImageBytes:
    """Decode real image bytes, enforce limits, strip metadata, and canonicalize."""

    content = bytes(raw)
    if not content:
        raise UploadValidationError("upload_empty", "The image is empty.")
    if len(content) > min(UPLOAD_MAX_BYTES, int(settings.upload_max_bytes)):
        raise UploadValidationError("upload_too_large", "The image exceeds 10 MiB.")

    magic_mime = _magic_mime(content)
    if magic_mime is None:
        raise UploadValidationError("upload_type_invalid", "Only JPEG, PNG, and WebP are accepted.")
    declared = str(declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared not in set(_ALLOWED_MIME_BY_FORMAT.values()):
        raise UploadValidationError("upload_type_invalid", "The declared image type is not accepted.")
    if declared != magic_mime:
        raise UploadValidationError("upload_type_mismatch", "Declared image type does not match its bytes.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(BytesIO(content))
        with probe:
            actual_mime = _ALLOWED_MIME_BY_FORMAT.get(str(probe.format or "").upper())
            if actual_mime != magic_mime:
                raise UploadValidationError(
                    "upload_type_mismatch",
                    "Decoded image format does not match its signature.",
                )
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > min(
                UPLOAD_MAX_PIXELS,
                int(settings.upload_max_pixels),
            ):
                raise UploadValidationError(
                    "upload_pixel_limit",
                    "The decoded image exceeds the pixel limit.",
                )
            if len(probe.info.get("icc_profile") or b"") > 1_048_576:
                raise UploadValidationError("upload_metadata_invalid", "The ICC profile is too large.")
            if len(probe.info.get("exif") or b"") > 262_144:
                raise UploadValidationError("upload_metadata_invalid", "The EXIF payload is too large.")
            probe.verify()

        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
            safe_image = _canonical_rgb(decoded)
            width, height = safe_image.size
            output = BytesIO()
            safe_image.save(
                output,
                format="JPEG",
                quality=92,
                optimize=True,
                progressive=True,
            )
    except UploadValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UploadValidationError("upload_pixel_limit", "The image exceeds the pixel limit.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise UploadValidationError("upload_decode_failed", "The image could not be decoded safely.") from exc

    canonical = output.getvalue()
    if not canonical or len(canonical) > min(UPLOAD_MAX_BYTES, int(settings.upload_max_bytes)):
        raise UploadValidationError("upload_too_large", "The canonical image exceeds 10 MiB.")
    return ValidatedImageBytes(
        content=canonical,
        mime_type="image/jpeg",
        width=width,
        height=height,
        byte_size=len(canonical),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def deterministic_object_key(
    *,
    owner_user_id: uuid.UUID,
    batch_id: uuid.UUID,
    part_ordinal: int,
    asset_id: uuid.UUID,
    mime_type: str,
) -> str:
    extension = "webp" if mime_type == "image/webp" else "jpg"
    return (
        f"users/{owner_user_id}/uploads/{batch_id}/"
        f"{int(part_ordinal):04d}-{asset_id}.{extension}"
    )


def issue_provider_grant_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_upload_batch(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    request_id: str,
) -> UploadBatch:
    return await upload_quota_service.create_upload_batch(
        db,
        user_id=user_id,
        request_id=request_id,
    )


async def store_validated_batch(
    db: AsyncSession,
    *,
    batch: UploadBatch,
    owner_user_id: uuid.UUID,
    validated_images: list[ValidatedImageBytes],
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> list[MediaAsset]:
    """Persist intents first, write every object, then activate all or none."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("upload activation time must be timezone-aware")
    if batch.owner_user_id != owner_user_id or not validated_images:
        raise UploadBatchError()
    if len(validated_images) > int(settings.upload_max_files):
        raise UploadBatchError("upload_file_count_limit")

    assets: list[MediaAsset] = []
    for ordinal, image in enumerate(validated_images):
        asset_id = uuid.uuid4()
        asset = MediaAsset(
            id=asset_id,
            owner_user_id=owner_user_id,
            upload_batch_id=batch.id,
            upload_part_ordinal=ordinal,
            role=MediaAssetRole.SOURCE,
            storage_provider=settings.effective_storage_provider,
            object_key=deterministic_object_key(
                owner_user_id=owner_user_id,
                batch_id=batch.id,
                part_ordinal=ordinal,
                asset_id=asset_id,
                mime_type=image.mime_type,
            ),
            sha256=image.sha256,
            mime_type=image.mime_type,
            byte_size=image.byte_size,
            width=image.width,
            height=image.height,
            access_level="private",
            policy_version="media-source-v1-7d",
            expires_at=current + timedelta(days=7),
            status=MediaAssetStatus.PENDING_UPLOAD,
        )
        assets.append(asset)
    db.add_all(assets)
    await db.flush()
    await db.commit()

    try:
        for asset, image in zip(assets, validated_images, strict=True):
            await asyncio.to_thread(
                object_store.put_private,
                asset.object_key,
                image.content,
                image.mime_type,
            )
        for asset in assets:
            asset.status = MediaAssetStatus.ACTIVE
        batch.status = UploadBatchStatus.ACTIVE
        batch.received_files = len(assets)
        await db.commit()
        return assets
    except Exception as exc:
        await db.rollback()
        batch.status = UploadBatchStatus.UPLOAD_FAILED
        batch.failure_code = "private_store_failed"
        for asset in assets:
            asset.status = MediaAssetStatus.UPLOAD_FAILED
        await db.commit()

        for asset in assets:
            asset.status = MediaAssetStatus.PENDING_DELETE
            asset.read_revoked_at = current
            asset.deletion_reason = "upload_rollback"
            asset.deletion_blockers = []
            asset.next_delete_at = current
        await db.commit()

        for asset in assets:
            result = await asyncio.to_thread(object_store.delete_private, asset.object_key)
            if result in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
                asset.status = MediaAssetStatus.DELETED
                asset.deleted_at = current
            else:
                asset.last_delete_error = "private_store_cleanup_failed"
        await db.commit()
        raise UploadBatchError() from exc


async def store_validated_upload(
    db: AsyncSession,
    *,
    batch: UploadBatch,
    owner_user_id: uuid.UUID,
    validated_image: ValidatedImageBytes,
    object_store: PrivateObjectStore = storage_service,
) -> MediaAsset:
    assets = await store_validated_batch(
        db,
        batch=batch,
        owner_user_id=owner_user_id,
        validated_images=[validated_image],
        object_store=object_store,
    )
    return assets[0]


async def activate_upload_batch(
    db: AsyncSession,
    batch: UploadBatch,
    assets: list[MediaAsset],
) -> None:
    if not assets or any(asset.status != MediaAssetStatus.PENDING_UPLOAD for asset in assets):
        raise UploadBatchError()
    for asset in assets:
        asset.status = MediaAssetStatus.ACTIVE
    batch.status = UploadBatchStatus.ACTIVE
    batch.received_files = len(assets)
    await db.commit()


class _MultipartReader:
    def __init__(self, request: Any) -> None:
        self._iterator = request.stream().__aiter__()
        self.buffer = bytearray()
        self.ended = False
        self.total_network_bytes = 0
        self.network_limit = (
            int(settings.upload_max_files) * int(settings.upload_max_bytes)
            + (_HEADER_LIMIT + 256) * (int(settings.upload_max_files) + 1)
        )

    async def _fill(self) -> None:
        if self.ended:
            return
        try:
            chunk = await self._iterator.__anext__()
        except StopAsyncIteration:
            self.ended = True
            return
        if not isinstance(chunk, (bytes, bytearray)):
            raise UploadValidationError("upload_multipart_invalid", "Upload stream returned invalid bytes.")
        self.total_network_bytes += len(chunk)
        if self.total_network_bytes > self.network_limit:
            raise UploadValidationError("upload_stream_limit", "The multipart body is too large.")
        self.buffer.extend(chunk)

    async def read_exact(self, length: int) -> bytes:
        while len(self.buffer) < length and not self.ended:
            await self._fill()
        if len(self.buffer) < length:
            raise UploadValidationError("upload_multipart_invalid", "Multipart body ended early.")
        value = bytes(self.buffer[:length])
        del self.buffer[:length]
        return value

    async def read_until(self, delimiter: bytes, *, limit: int) -> bytes:
        while True:
            position = self.buffer.find(delimiter)
            if position >= 0:
                value = bytes(self.buffer[:position])
                del self.buffer[: position + len(delimiter)]
                return value
            if len(self.buffer) > limit:
                raise UploadValidationError("upload_multipart_invalid", "Multipart headers are too large.")
            if self.ended:
                raise UploadValidationError("upload_multipart_invalid", "Multipart delimiter is missing.")
            await self._fill()

    async def read_part_body(
        self,
        delimiter: bytes,
        *,
        attempted: list[int],
        progress,
    ) -> bytes:
        output = bytearray()
        next_progress = _PROGRESS_STEP
        tail_length = max(1, len(delimiter) - 1)
        while True:
            position = self.buffer.find(delimiter)
            if position >= 0:
                output.extend(self.buffer[:position])
                attempted[0] += position
                del self.buffer[: position + len(delimiter)]
                await progress(attempted[0])
                if attempted[0] > int(settings.upload_max_bytes):
                    raise UploadValidationError("upload_too_large", "The image exceeds 10 MiB.")
                return bytes(output)

            if len(self.buffer) > tail_length:
                safe_length = len(self.buffer) - tail_length
                output.extend(self.buffer[:safe_length])
                del self.buffer[:safe_length]
                attempted[0] += safe_length
                if attempted[0] >= next_progress:
                    await progress(attempted[0])
                    next_progress = ((attempted[0] // _PROGRESS_STEP) + 1) * _PROGRESS_STEP
                if attempted[0] > int(settings.upload_max_bytes):
                    await progress(attempted[0])
                    raise UploadValidationError("upload_too_large", "The image exceeds 10 MiB.")
            if self.ended:
                raise UploadValidationError("upload_multipart_invalid", "Multipart body ended early.")
            await self._fill()


def _multipart_boundary(content_type: str) -> bytes:
    media_type, options = parse_options_header(str(content_type or "").encode("latin-1"))
    boundary = options.get(b"boundary")
    if media_type.lower() != b"multipart/form-data" or not boundary:
        raise UploadValidationError("upload_multipart_invalid", "A multipart boundary is required.")
    if len(boundary) > _BOUNDARY_LIMIT or not _BOUNDARY_PATTERN.fullmatch(boundary):
        raise UploadValidationError("upload_multipart_invalid", "The multipart boundary is invalid.")
    return boundary


def _part_headers(raw: bytes) -> tuple[str, str]:
    headers: dict[bytes, bytes] = {}
    for line in raw.split(b"\r\n"):
        if not line or len(line) > _HEADER_LINE_LIMIT or b":" not in line:
            raise UploadValidationError("upload_multipart_invalid", "A multipart header is invalid.")
        name, value = line.split(b":", 1)
        key = name.strip().lower()
        if key in headers:
            raise UploadValidationError("upload_multipart_invalid", "Duplicate multipart header.")
        headers[key] = value.strip()
    disposition, options = parse_options_header(headers.get(b"content-disposition", b""))
    field_name = (options.get(b"name") or b"").decode("utf-8", "strict")
    filename = options.get(b"filename")
    if disposition.lower() != b"form-data" or field_name not in {"file", "files"} or filename is None:
        raise UploadValidationError("upload_multipart_invalid", "Only image file parts are accepted.")
    content_type = headers.get(b"content-type", b"").decode("latin-1").strip().lower()
    return field_name, content_type


async def stream_authenticated_multipart_upload(
    request: Any,
    user: User,
    db: AsyncSession,
    *,
    object_store: PrivateObjectStore = storage_service,
) -> dict[str, Any]:
    """Admit quota before touching Request.stream(), then process an all-or-none batch."""

    request_id = str(getattr(getattr(request, "state", None), "request_id", "") or uuid.uuid4())
    batch = await upload_quota_service.create_upload_batch(
        db,
        user_id=user.id,
        request_id=request_id,
    )
    failure_code = "upload_batch_rejected"
    try:
        boundary = _multipart_boundary(request.headers.get("content-type") or "")
        reader = _MultipartReader(request)
        initial = await reader.read_exact(len(boundary) + 4)
        if initial != b"--" + boundary + b"\r\n":
            raise UploadValidationError("upload_multipart_invalid", "Multipart opening boundary is invalid.")

        validated_images: list[ValidatedImageBytes] = []
        part_ordinal = 0
        delimiter = b"\r\n--" + boundary
        while True:
            if part_ordinal >= int(settings.upload_max_files):
                raise UploadValidationError("upload_file_count_limit", "At most five files are accepted.")
            raw_headers = await reader.read_until(b"\r\n\r\n", limit=_HEADER_LIMIT)
            _field_name, declared_type = _part_headers(raw_headers)
            reservation = await upload_quota_service.reserve_upload_part(
                db,
                batch=batch,
                part_ordinal=part_ordinal,
            )
            attempted = [0]

            async def persist_progress(value: int) -> None:
                await upload_quota_service.record_upload_progress(db, reservation, value)

            try:
                raw_body = await reader.read_part_body(
                    delimiter,
                    attempted=attempted,
                    progress=persist_progress,
                )
            finally:
                await upload_quota_service.settle_upload_part(
                    db,
                    reservation,
                    actual_attempted_bytes=attempted[0],
                )
            validated_images.append(
                validate_and_reencode_image(
                    raw_body,
                    declared_content_type=declared_type,
                )
            )
            part_ordinal += 1
            marker = await reader.read_exact(2)
            if marker == b"--":
                if reader.buffer.startswith(b"\r\n"):
                    del reader.buffer[:2]
                while not reader.ended:
                    await reader._fill()
                if reader.buffer:
                    raise UploadValidationError(
                        "upload_multipart_invalid",
                        "Multipart body contains trailing data.",
                    )
                break
            if marker != b"\r\n":
                raise UploadValidationError("upload_multipart_invalid", "Multipart boundary trailer is invalid.")

        assets = await store_validated_batch(
            db,
            batch=batch,
            owner_user_id=user.id,
            validated_images=validated_images,
            object_store=object_store,
        )
        return {
            "batch_id": str(batch.id),
            "assets": [
                {
                    "asset_id": str(asset.id),
                    "width": asset.width,
                    "height": asset.height,
                    "mime_type": asset.mime_type,
                    "byte_size": asset.byte_size,
                    "expires_at": asset.expires_at,
                }
                for asset in assets
            ],
        }
    except upload_quota_service.UploadQuotaExceeded:
        failure_code = "upload_quota_exceeded"
        raise
    except UploadValidationError as exc:
        failure_code = exc.code
        raise
    finally:
        if batch.status == UploadBatchStatus.PENDING_UPLOAD:
            batch.status = UploadBatchStatus.UPLOAD_FAILED
            batch.failure_code = failure_code
            await db.commit()
        await upload_quota_service.release_upload_slot(db, batch)


async def _asset_by_id(db: AsyncSession, asset_id: uuid.UUID) -> MediaAsset | None:
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    return result.scalar_one_or_none()


def authorize_owner_asset_read(
    user: User,
    asset: MediaAsset,
    *,
    now: datetime | None = None,
) -> MediaAsset:
    """Authorize only the private source-upload read surface available before Task 20."""

    current = now or datetime.now(timezone.utc)
    if asset.owner_user_id != user.id:
        raise AssetAccessError("asset_forbidden")
    if MediaAssetRole(asset.role) != MediaAssetRole.SOURCE:
        raise AssetAccessError("asset_role_forbidden")
    if (
        MediaAssetStatus(asset.status) != MediaAssetStatus.ACTIVE
        or asset.read_revoked_at is not None
        or asset.expires_at <= current
    ):
        raise AssetAccessError("asset_unavailable")
    return asset


async def load_owner_source_asset(
    db: AsyncSession,
    *,
    user: User,
    asset_id: uuid.UUID,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> PrivateAssetBytes:
    asset = await _asset_by_id(db, asset_id)
    if asset is None:
        raise AssetAccessError("asset_not_found")
    authorize_owner_asset_read(user, asset, now=now)
    content = await asyncio.to_thread(object_store.read_private, asset.object_key)
    if len(content) != int(asset.byte_size) or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise AssetAccessError("asset_integrity_failed")
    return PrivateAssetBytes(asset=asset, content=content, mime_type=asset.mime_type)


async def request_owner_asset_deletion(
    db: AsyncSession,
    *,
    user: User,
    asset_id: uuid.UUID,
    now: datetime | None = None,
) -> DeletionRequestResult:
    """Verify ownership before entering the shared deletion state machine."""

    asset = await _asset_by_id(db, asset_id)
    if asset is None:
        raise AssetAccessError("asset_not_found")
    if asset.owner_user_id != user.id:
        raise AssetAccessError("asset_forbidden")
    return await request_asset_deletion(
        db,
        asset.id,
        reason="user_request",
        now=now,
    )


async def load_owned_asset_bytes(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    asset_id: uuid.UUID,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> PrivateAssetBytes:
    current = now or datetime.now(timezone.utc)
    asset = await _asset_by_id(db, asset_id)
    if asset is None:
        raise AssetAccessError("asset_not_found")
    if asset.owner_user_id != owner_user_id:
        raise AssetAccessError("asset_forbidden")
    if (
        asset.status != MediaAssetStatus.ACTIVE
        or asset.read_revoked_at is not None
        or asset.expires_at <= current
    ):
        raise AssetAccessError("asset_unavailable")
    content = await asyncio.to_thread(object_store.read_private, asset.object_key)
    if len(content) != int(asset.byte_size) or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise AssetAccessError("asset_integrity_failed")
    return PrivateAssetBytes(asset=asset, content=content, mime_type=asset.mime_type)


async def create_provider_grant(
    db: AsyncSession,
    *,
    asset: MediaAsset,
    provider: str,
    purpose: str,
    job_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    commit: bool = True,
    now: datetime | None = None,
) -> IssuedAssetGrant:
    current = now or datetime.now(timezone.utc)
    if (
        asset.status != MediaAssetStatus.ACTIVE
        or asset.read_revoked_at is not None
        or asset.expires_at <= current
    ):
        raise AssetAccessError("asset_unavailable")
    if settings.runtime_environment != "development" and settings.runtime_coordinate_errors:
        raise AssetAccessError("runtime_identity_invalid")
    base = settings.effective_provider_grant_origin.rstrip("/")
    if settings.runtime_environment != "development" and not base.startswith("https://"):
        raise AssetAccessError("runtime_identity_invalid")
    clean_provider = str(provider or "").strip().lower()
    clean_purpose = str(purpose or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{1,64}", clean_provider) or not re.fullmatch(
        r"[a-z0-9_.-]{1,64}", clean_purpose
    ):
        raise ValueError("provider grant binding is invalid")
    generation_binding_requested = (
        clean_provider == "evolink" or clean_purpose == "generation-input"
    )
    if generation_binding_requested and (
        clean_provider != "evolink"
        or clean_purpose != "generation-input"
        or job_id is None
        or attempt_id is None
    ):
        raise ValueError("generation provider grant binding is invalid")
    token, token_hash = issue_provider_grant_token()
    grant = AssetAccessGrant(
        id=uuid.uuid4(),
        asset_id=asset.id,
        token_hash=token_hash,
        provider=clean_provider,
        purpose=clean_purpose,
        job_id=job_id,
        attempt_id=attempt_id,
        runtime_bundle_id=settings.runtime_bundle_id.strip() or "development",
        target_api_deployment_id=settings.deployment_id or "development",
        serving_deployment_role=settings.release_role.strip() or "DEVELOPMENT",
        expires_at=min(
            asset.expires_at,
            current + timedelta(seconds=int(settings.provider_asset_grant_ttl_seconds)),
        ),
        max_reads=int(settings.provider_asset_grant_max_reads),
        used_count=0,
    )
    db.add(grant)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return IssuedAssetGrant(
        grant=grant,
        token=token,
        read_url=f"{base}/api/v1/media/grants/{token}",
    )


async def revoke_provider_grant(
    db: AsyncSession,
    grant: AssetAccessGrant,
    *,
    now: datetime | None = None,
) -> None:
    if grant.revoked_at is None:
        grant.revoked_at = now or datetime.now(timezone.utc)
        await db.commit()


async def stream_provider_grant(
    db: AsyncSession,
    *,
    token: str,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> PrivateAssetBytes:
    current = now or datetime.now(timezone.utc)
    clean = str(token or "")
    if len(clean) < 43 or len(clean) > 128:
        raise AssetAccessError("asset_grant_invalid")
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(AssetAccessGrant)
        .where(AssetAccessGrant.token_hash == token_hash)
        .with_for_update()
    )
    grant = result.scalar_one_or_none()
    if (
        grant is None
        or grant.revoked_at is not None
        or grant.expires_at <= current
        or grant.used_count >= grant.max_reads
    ):
        raise AssetAccessError("asset_grant_invalid")
    generation_binding_present = (
        grant.provider == "evolink" or grant.purpose == "generation-input"
    )
    if generation_binding_present and (
        grant.provider != "evolink"
        or grant.purpose != "generation-input"
        or grant.job_id is None
        or grant.attempt_id is None
    ):
        raise AssetAccessError("asset_grant_invalid")
    if settings.runtime_environment != "development" and (
        grant.runtime_bundle_id != settings.runtime_bundle_id.strip()
        or grant.target_api_deployment_id != settings.deployment_id
        or grant.serving_deployment_role != settings.release_role.strip()
    ):
        raise AssetAccessError("asset_grant_invalid")
    asset = await _asset_by_id(db, grant.asset_id)
    if (
        asset is None
        or asset.status != MediaAssetStatus.ACTIVE
        or asset.read_revoked_at is not None
        or asset.expires_at <= current
    ):
        raise AssetAccessError("asset_grant_invalid")
    grant.used_count += 1
    grant.last_used_at = current
    await db.commit()
    content = await asyncio.to_thread(object_store.read_private, asset.object_key)
    if len(content) != int(asset.byte_size) or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise AssetAccessError("asset_integrity_failed")
    return PrivateAssetBytes(asset=asset, content=content, mime_type=asset.mime_type)


async def store_private_derivative(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    parent_asset: MediaAsset,
    role: MediaAssetRole,
    validated: ValidatedImageBytes,
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> MediaAsset:
    current = now or datetime.now(timezone.utc)
    asset_id = uuid.uuid4()
    asset = MediaAsset(
        id=asset_id,
        owner_user_id=owner_user_id,
        order_id=parent_asset.order_id,
        job_id=parent_asset.job_id,
        parent_asset_id=parent_asset.id,
        role=role,
        storage_provider=settings.effective_storage_provider,
        object_key=(
            f"users/{owner_user_id}/derivatives/{parent_asset.id}/"
            f"{role.value}-{asset_id}.jpg"
        ),
        sha256=validated.sha256,
        mime_type=validated.mime_type,
        byte_size=validated.byte_size,
        width=validated.width,
        height=validated.height,
        access_level="private",
        policy_version=parent_asset.policy_version,
        expires_at=parent_asset.expires_at,
        status=MediaAssetStatus.PENDING_UPLOAD,
    )
    db.add(asset)
    await db.commit()
    try:
        await asyncio.to_thread(
            object_store.put_private,
            asset.object_key,
            validated.content,
            validated.mime_type,
        )
        asset.status = MediaAssetStatus.ACTIVE
        await db.commit()
        return asset
    except Exception as exc:
        asset.status = MediaAssetStatus.UPLOAD_FAILED
        await db.commit()
        asset.status = MediaAssetStatus.PENDING_DELETE
        asset.read_revoked_at = current
        asset.deletion_reason = "derivative_rollback"
        asset.deletion_blockers = []
        asset.next_delete_at = current
        await db.commit()
        result = await asyncio.to_thread(object_store.delete_private, asset.object_key)
        if result in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
            asset.status = MediaAssetStatus.DELETED
            asset.deleted_at = current
            asset.next_delete_at = None
        else:
            asset.last_delete_error = "private_store_cleanup_failed"
        await db.commit()
        raise UploadBatchError("derivative_store_failed") from exc


async def store_private_derivatives(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    parent_asset: MediaAsset,
    derivatives: list[tuple[MediaAssetRole, ValidatedImageBytes]],
    object_store: PrivateObjectStore = storage_service,
    now: datetime | None = None,
) -> list[MediaAsset]:
    """Persist and activate a derivative set atomically from the caller's perspective."""

    current = now or datetime.now(timezone.utc)
    if parent_asset.owner_user_id != owner_user_id or not derivatives:
        raise UploadBatchError("derivative_batch_invalid")
    assets: list[MediaAsset] = []
    for role, validated in derivatives:
        asset_id = uuid.uuid4()
        assets.append(
            MediaAsset(
                id=asset_id,
                owner_user_id=owner_user_id,
                order_id=parent_asset.order_id,
                job_id=parent_asset.job_id,
                parent_asset_id=parent_asset.id,
                role=role,
                storage_provider=settings.effective_storage_provider,
                object_key=(
                    f"users/{owner_user_id}/derivatives/{parent_asset.id}/"
                    f"{role.value}-{asset_id}.jpg"
                ),
                sha256=validated.sha256,
                mime_type=validated.mime_type,
                byte_size=validated.byte_size,
                width=validated.width,
                height=validated.height,
                access_level="private",
                policy_version=parent_asset.policy_version,
                expires_at=parent_asset.expires_at,
                status=MediaAssetStatus.PENDING_UPLOAD,
            )
        )
    db.add_all(assets)
    await db.commit()
    try:
        for asset, (_role, validated) in zip(assets, derivatives, strict=True):
            await asyncio.to_thread(
                object_store.put_private,
                asset.object_key,
                validated.content,
                validated.mime_type,
            )
        for asset in assets:
            asset.status = MediaAssetStatus.ACTIVE
        await db.commit()
        return assets
    except Exception as exc:
        for asset in assets:
            asset.status = MediaAssetStatus.UPLOAD_FAILED
        await db.commit()
        for asset in assets:
            asset.status = MediaAssetStatus.PENDING_DELETE
            asset.read_revoked_at = current
            asset.deletion_reason = "derivative_batch_rollback"
            asset.deletion_blockers = []
            asset.next_delete_at = current
        await db.commit()
        for asset in assets:
            result = await asyncio.to_thread(object_store.delete_private, asset.object_key)
            if result in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
                asset.status = MediaAssetStatus.DELETED
                asset.deleted_at = current
                asset.next_delete_at = None
            else:
                asset.last_delete_error = "private_store_cleanup_failed"
        await db.commit()
        raise UploadBatchError("derivative_batch_store_failed") from exc
