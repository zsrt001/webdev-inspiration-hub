#!/usr/bin/env python3
"""Create one isolated real private-object case for the Preview Provider fetch proof."""

from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for entry in (str(ROOT), str(BACKEND)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import normalize_database_url  # noqa: E402
from app.models.acceptance_identity_binding import AcceptanceIdentityBinding  # noqa: E402
from app.models.generation_attempt import (  # noqa: E402
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus  # noqa: E402
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus  # noqa: E402
from app.models.order import Order, OrderStatus  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_identity import UserIdentity  # noqa: E402
from app.services.media_asset_service import (  # noqa: E402
    UploadValidationError,
    validate_and_reencode_image,
)
from app.services.storage import StorageService  # noqa: E402


CASE_NAMESPACE = "vowpic.preview-provider-fetch.v1"
CASE_TEMPLATE = "provider-fetch-acceptance"
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validate_activation(activation: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "phase": "COMPLETED",
        "api_role": "PREVIEW_COMMERCIAL_API",
        "worker_role": "PREVIEW_COMMERCIAL_WORKER",
    }
    if not isinstance(activation, dict) or any(
        activation.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("Provider case requires a completed PREVIEW_COMMERCIAL activation")
    uuid.UUID(str(activation.get("activation_id") or ""))
    if not _SOURCE_SHA.fullmatch(str(activation.get("source_sha") or "")):
        raise ValueError("Provider case activation source SHA is invalid")
    if not _RUNTIME_ID.fullmatch(str(activation.get("runtime_bundle_id") or "")):
        raise ValueError("Provider case activation runtime ID is invalid")
    for field in ("api_deployment_id", "worker_deployment_id"):
        if not _DEPLOYMENT_ID.fullmatch(str(activation.get(field) or "")):
            raise ValueError(f"Provider case activation {field} is invalid")
    if not _DIGEST.fullmatch(str(activation.get("worker_image_digest") or "")):
        raise ValueError("Provider case activation Worker digest is invalid")
    return dict(activation)


def case_id_for_activation(activation_id: object) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(str(activation_id)), CASE_NAMESPACE)


def provider_case_object_key(activation_id: object, case_id: object) -> str:
    activation_uuid = uuid.UUID(str(activation_id))
    case_uuid = uuid.UUID(str(case_id))
    if case_uuid != case_id_for_activation(activation_uuid):
        raise ValueError("Provider case ID is not derived from the activation")
    return f"acceptance/provider-fetch/{activation_uuid}/{case_uuid}/source.jpg"


def build_network_probe_image() -> bytes:
    """Return a deterministic non-personal PNG used only to prove Provider network fetch."""

    image = Image.new("RGB", (128, 128))
    pixels = image.load()
    for y in range(128):
        for x in range(128):
            pixels[x, y] = ((x * 2) % 256, (y * 2) % 256, ((x + y) * 3) % 256)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def build_case_reference(
    activation: dict[str, Any],
    *,
    owner_user_id: uuid.UUID,
    order_id: uuid.UUID,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> dict[str, Any]:
    normalized = _validate_activation(activation)
    for value in (owner_user_id, order_id, job_id, attempt_id, asset_id):
        if not isinstance(value, uuid.UUID):
            raise ValueError("Provider case row coordinates must be UUIDs")
    case_id = case_id_for_activation(normalized["activation_id"])
    return {
        "schema": "vowpic.preview-provider-input.v1",
        "activation_id": str(normalized["activation_id"]),
        "case_id": str(case_id),
        "source_sha": str(normalized["source_sha"]),
        "runtime_bundle_id": str(normalized["runtime_bundle_id"]),
        "api_deployment_id": str(normalized["api_deployment_id"]),
        "worker_deployment_id": str(normalized["worker_deployment_id"]),
        "worker_image_digest": str(normalized["worker_image_digest"]),
        "job_id": str(job_id),
        "attempt_id": str(attempt_id),
        "asset_id": str(asset_id),
    }


def _runtime_matches(activation: dict[str, Any]) -> bool:
    settings = get_settings()
    return (
        settings.runtime_environment == "preview"
        and settings.release_role.strip() == "PREVIEW_COMMERCIAL"
        and settings.source_sha == activation["source_sha"]
        and settings.runtime_bundle_id.strip() == activation["runtime_bundle_id"]
        and settings.deployment_id == activation["api_deployment_id"]
        and settings.worker_image_digest.strip() == activation["worker_image_digest"]
        and settings.provider_asset_grant_max_reads == 1
    )


async def _require_acceptance_owner(db, owner_user_id: uuid.UUID) -> None:
    user = await db.scalar(select(User).where(User.id == owner_user_id).with_for_update())
    if user is None or str(user.status or "").lower() != "active":
        raise ValueError("Provider case owner is not an active user")
    identity_id = await db.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == owner_user_id,
            UserIdentity.provider == "supabase",
            UserIdentity.revoked_at.is_(None),
        ).limit(1)
    )
    binding_id = await db.scalar(
        select(AcceptanceIdentityBinding.id).where(
            AcceptanceIdentityBinding.consumed_user_id == owner_user_id,
            AcceptanceIdentityBinding.provider == "google",
            AcceptanceIdentityBinding.consumed_at.is_not(None),
        ).limit(1)
    )
    if identity_id is None or binding_id is None:
        raise ValueError("Provider case owner lacks a consumed Google acceptance binding")


def _case_reference_from_rows(
    activation: dict[str, Any],
    *,
    owner_user_id: uuid.UUID,
    order: Order,
    job: GenerationJob,
    attempt: GenerationAttempt,
    asset: MediaAsset,
) -> dict[str, Any]:
    reference = build_case_reference(
        activation,
        owner_user_id=owner_user_id,
        order_id=order.id,
        job_id=job.id,
        attempt_id=attempt.id,
        asset_id=asset.id,
    )
    if (
        order.user_id != owner_user_id
        or job.order_id != order.id
        or attempt.job_id != job.id
        or asset.owner_user_id != owner_user_id
        or asset.order_id != order.id
        or asset.job_id != job.id
    ):
        raise ValueError("Provider case graph ownership mismatch")
    if (
        order.template_id != CASE_TEMPLATE
        or order.style_template != CASE_TEMPLATE
        or order.source_asset_ids != [str(asset.id)]
        or OrderStatus(order.status) is not OrderStatus.QUEUED
        or int(order.price_cents or 0) != 0
        or order.payment_id is not None
        or order.paid_at is not None
        or order.reservation_id is not None
        or GenerationJobStatus(job.status) is not GenerationJobStatus.QUEUED
        or job.active_attempt_id != attempt.id
        or job.lease_owner is not None
        or job.api_deployment_id != reference["api_deployment_id"]
        or job.runtime_bundle_id != reference["runtime_bundle_id"]
        or job.expected_worker_image_digest != reference["worker_image_digest"]
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.PREPARED
        or attempt.provider != "evolink"
        or attempt.provider_job_id is not None
        or MediaAssetStatus(asset.status)
        not in {MediaAssetStatus.PENDING_UPLOAD, MediaAssetStatus.ACTIVE}
        or asset.object_key
        != provider_case_object_key(reference["activation_id"], reference["case_id"])
    ):
        raise ValueError("Provider case graph is not an isolated non-financial acceptance case")
    from scripts.release.prepare_preview_provider_grant import validate_input_reference

    return validate_input_reference(reference)


async def prepare_case(
    database_url: str,
    activation: dict[str, Any],
    *,
    owner_user_id: uuid.UUID,
    image_bytes: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = _validate_activation(activation)
    if not _runtime_matches(normalized):
        raise ValueError("Provider case does not match this Preview Commercial runtime")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Provider case timestamp must be timezone-aware")
    if image_bytes.startswith(b"\xff\xd8\xff"):
        declared_mime = "image/jpeg"
    elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        declared_mime = "image/png"
    elif len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        declared_mime = "image/webp"
    else:
        raise ValueError("Provider case image must be JPEG, PNG, or WebP")
    image = validate_and_reencode_image(image_bytes, declared_content_type=declared_mime)
    case_id = case_id_for_activation(normalized["activation_id"])
    object_key = provider_case_object_key(normalized["activation_id"], case_id)
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    store = StorageService()
    try:
        async with session_factory() as db:
            async with db.begin():
                activation_row = await db.scalar(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == uuid.UUID(normalized["activation_id"]))
                    .with_for_update()
                )
                exact = {
                    "environment": "preview",
                    "kind": "PREVIEW_COMMERCIAL",
                    "phase": "COMPLETED",
                    "source_sha": normalized["source_sha"],
                    "runtime_bundle_id": normalized["runtime_bundle_id"],
                    "api_deployment_id": normalized["api_deployment_id"],
                    "worker_deployment_id": normalized["worker_deployment_id"],
                    "worker_image_digest": normalized["worker_image_digest"],
                }
                if (
                    activation_row is None
                    or any(str(getattr(activation_row, key)) != str(value) for key, value in exact.items())
                    or activation_row.current_snapshot_hash is None
                    or activation_row.current_snapshot_hash != activation_row.target_snapshot_hash
                ):
                    raise ValueError("Provider case activation is not exact and all-OFF")
                await _require_acceptance_owner(db, owner_user_id)
                asset = await db.scalar(
                    select(MediaAsset).where(MediaAsset.object_key == object_key).with_for_update()
                )
                if asset is None:
                    order = Order(
                        id=uuid.uuid4(),
                        user_id=owner_user_id,
                        status=OrderStatus.QUEUED,
                        template_id=CASE_TEMPLATE,
                        style_template=CASE_TEMPLATE,
                        source_asset_ids=[],
                        product_policy_snapshot={
                            "schema": "vowpic.provider-fetch-acceptance.v1",
                            "case_id": str(case_id),
                        },
                        funding_policy_snapshot={"mode": "none", "reason": CASE_TEMPLATE},
                        settlement_status="UNSETTLED",
                        delivery_status="PENDING",
                        price_cents=0,
                    )
                    db.add(order)
                    await db.flush()
                    job = GenerationJob.queued(
                        order_id=order.id,
                        submission_correlation_id=uuid.uuid4(),
                        api_deployment_id=normalized["api_deployment_id"],
                        runtime_bundle_id=normalized["runtime_bundle_id"],
                        expected_worker_image_digest=normalized["worker_image_digest"],
                    )
                    db.add(job)
                    await db.flush()
                    order.generation_job_id = job.id
                    attempt = GenerationAttempt.prepared(
                        job=job,
                        attempt_number=1,
                        kind=GenerationAttemptKind.INITIAL,
                        provider="evolink",
                    )
                    db.add(attempt)
                    await db.flush()
                    job.active_attempt_id = attempt.id
                    asset = MediaAsset(
                        id=uuid.uuid4(),
                        owner_user_id=owner_user_id,
                        order_id=order.id,
                        job_id=job.id,
                        role=MediaAssetRole.SOURCE,
                        storage_provider=get_settings().effective_storage_provider,
                        object_key=object_key,
                        sha256=image.sha256,
                        mime_type=image.mime_type,
                        byte_size=image.byte_size,
                        width=image.width,
                        height=image.height,
                        access_level="private",
                        policy_version="provider-fetch-acceptance-v1",
                        expires_at=current + timedelta(hours=2),
                        status=MediaAssetStatus.PENDING_UPLOAD,
                    )
                    db.add(asset)
                    order.source_asset_ids = [str(asset.id)]
                    await db.flush()
                else:
                    order = await db.scalar(select(Order).where(Order.id == asset.order_id).with_for_update())
                    job = await db.scalar(select(GenerationJob).where(GenerationJob.id == asset.job_id).with_for_update())
                    attempt = await db.scalar(
                        select(GenerationAttempt)
                        .where(GenerationAttempt.id == job.active_attempt_id)
                        .with_for_update()
                    ) if job is not None else None
                    if order is None or job is None or attempt is None:
                        raise ValueError("Provider case recovery graph is incomplete")
                    if asset.sha256 != image.sha256 or asset.status not in {
                        MediaAssetStatus.PENDING_UPLOAD,
                        MediaAssetStatus.ACTIVE,
                    }:
                        raise ValueError("Provider case recovery asset mismatch")
                reference = _case_reference_from_rows(
                    normalized,
                    owner_user_id=owner_user_id,
                    order=order,
                    job=job,
                    attempt=attempt,
                    asset=asset,
                )

        prefix = object_key.rsplit("/", 1)[0] + "/"
        existing = await asyncio.to_thread(store.list_private, prefix, limit=2)
        if not existing:
            await asyncio.to_thread(store.put_private, object_key, image.content, image.mime_type)
        elif existing != (object_key,):
            raise ValueError("Provider case object prefix contains unexpected objects")
        stored = await asyncio.to_thread(store.read_private, object_key)
        if stored != image.content:
            raise ValueError("Provider case private object read-back mismatch")

        async with session_factory() as db:
            async with db.begin():
                asset = await db.scalar(
                    select(MediaAsset).where(MediaAsset.id == uuid.UUID(reference["asset_id"])).with_for_update()
                )
                if asset is None or asset.object_key != object_key or asset.sha256 != image.sha256:
                    raise ValueError("Provider case asset disappeared before activation")
                if MediaAssetStatus(asset.status) is MediaAssetStatus.PENDING_UPLOAD:
                    asset.status = MediaAssetStatus.ACTIVE
                elif MediaAssetStatus(asset.status) is not MediaAssetStatus.ACTIVE:
                    raise ValueError("Provider case asset is not activatable")
        return reference
    finally:
        await engine.dispose()


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider case activation input must be an object")
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-json", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_MIGRATION_DATABASE_URL")
    parser.add_argument("--owner-user-id-env", default="PREVIEW_PROVIDER_OWNER_USER_ID")
    parser.add_argument("--image-file")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "").strip()
        owner_user_id = uuid.UUID(os.environ.get(args.owner_user_id_env, "").strip())
        if not database_url:
            raise ValueError("Provider case database is required")
        image_bytes = (
            Path(args.image_file).read_bytes()
            if args.image_file
            else build_network_probe_image()
        )
        result = asyncio.run(
            prepare_case(
                database_url,
                _load_json(args.activation_json),
                owner_user_id=owner_user_id,
                image_bytes=image_bytes,
            )
        )
        _write_create_once(Path(args.output), result)
        print(json.dumps({"state": "PREPARED", "case_id": result["case_id"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, UploadValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
