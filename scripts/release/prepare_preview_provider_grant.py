#!/usr/bin/env python3
"""Create one Preview Provider grant from a backend-bound test journey."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import normalize_database_url  # noqa: E402
from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus  # noqa: E402
from app.models.generation_job import GenerationJob, GenerationJobStatus  # noqa: E402
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.services.media_asset_service import IssuedAssetGrant, create_provider_grant  # noqa: E402


INPUT_KEYS = {
    "schema", "activation_id", "case_id", "source_sha", "runtime_bundle_id", "api_deployment_id",
    "backend_executor_digest", "job_id", "attempt_id", "asset_id",
}
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_input_reference(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != INPUT_KEYS:
        raise ValueError("Preview Provider input reference schema is invalid")
    if payload.get("schema") != "vowpic.preview-provider-input.v1":
        raise ValueError("Preview Provider input reference version is invalid")
    for field in ("activation_id", "case_id", "job_id", "attempt_id", "asset_id"):
        try:
            UUID(str(payload.get(field) or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Preview Provider input {field} is invalid") from exc
    if not _SOURCE_SHA.fullmatch(str(payload.get("source_sha") or "")):
        raise ValueError("Preview Provider input source SHA is invalid")
    if not _RUNTIME_ID.fullmatch(str(payload.get("runtime_bundle_id") or "")):
        raise ValueError("Preview Provider input runtime ID is invalid")
    if not _DEPLOYMENT.fullmatch(str(payload.get("api_deployment_id") or "")):
        raise ValueError("Preview Provider input API deployment ID is invalid")
    if not _DIGEST.fullmatch(str(payload.get("backend_executor_digest") or "")):
        raise ValueError("Preview Provider input backend executor digest is invalid")
    return dict(payload)


def provider_case_prefix(reference: dict[str, Any]) -> str:
    normalized = validate_input_reference(reference)
    return (
        f"acceptance/provider-fetch/{normalized['activation_id']}/"
        f"{normalized['case_id']}/"
    )


def is_provider_case_object_key(reference: dict[str, Any], object_key: object) -> bool:
    prefix = provider_case_prefix(reference)
    return bool(
        re.fullmatch(
            re.escape(prefix) + r"source\.(?:jpg|jpeg|png|webp)",
            str(object_key or ""),
        )
    )


def build_grant_reference(
    input_reference: dict[str, Any], issued: IssuedAssetGrant
) -> dict[str, Any]:
    normalized = validate_input_reference(input_reference)
    UUID(str(issued.grant.id))
    read_url = str(issued.read_url or "").strip()
    if not read_url.startswith("https://") or "/api/v1/media/grants/" not in read_url:
        raise ValueError("Preview Provider grant URL is invalid")
    return {
        "schema": "vowpic.provider-grant-reference.v1",
        "activation_id": normalized["activation_id"],
        "case_id": normalized["case_id"],
        "source_sha": normalized["source_sha"],
        "runtime_bundle_id": normalized["runtime_bundle_id"],
        "api_deployment_id": normalized["api_deployment_id"],
        "backend_executor_digest": normalized["backend_executor_digest"],
        "grant_id": str(issued.grant.id),
        "asset_id": normalized["asset_id"],
        "job_id": normalized["job_id"],
        "attempt_id": normalized["attempt_id"],
        "read_url": read_url,
    }


def _runtime_matches(reference: dict[str, Any]) -> bool:
    settings = get_settings()
    return (
        settings.runtime_environment == "preview"
        and settings.release_role.strip() == "PREVIEW_COMMERCIAL"
        and settings.source_sha == reference["source_sha"]
        and settings.runtime_bundle_id.strip() == reference["runtime_bundle_id"]
        and settings.deployment_id == reference["api_deployment_id"]
        and settings.backend_executor_digest == reference["backend_executor_digest"]
        and settings.effective_provider_grant_origin.startswith("https://vowpic-provider-")
    )


async def prepare_grant(
    database_url: str,
    reference: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = validate_input_reference(reference)
    if not _runtime_matches(normalized):
        raise ValueError("Preview Provider input does not match this process runtime")
    current = now or datetime.now(timezone.utc)
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            async with db.begin():
                activation = await db.scalar(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == UUID(normalized["activation_id"]))
                    .with_for_update()
                )
                activation_expected = {
                    "environment": "preview",
                    "kind": "PREVIEW_COMMERCIAL",
                    "phase": "COMPLETED",
                    "source_sha": normalized["source_sha"],
                    "runtime_bundle_id": normalized["runtime_bundle_id"],
                    "api_deployment_id": normalized["api_deployment_id"],
                    "worker_deployment_id": None,
                    "worker_role": None,
                    "worker_image_digest": None,
                }
                if (
                    activation is None
                    or any(
                        str(getattr(activation, key)) != str(value)
                        for key, value in activation_expected.items()
                    )
                    or activation.current_snapshot_hash is None
                    or activation.current_snapshot_hash != activation.target_snapshot_hash
                ):
                    raise ValueError("Preview Provider activation is not exact and all-OFF")
                job = await db.scalar(
                    select(GenerationJob)
                    .where(GenerationJob.id == UUID(normalized["job_id"]))
                    .with_for_update()
                )
                if (
                    job is None
                    or GenerationJobStatus(job.status)
                    not in {GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE}
                    or job.runtime_bundle_id != normalized["runtime_bundle_id"]
                    or job.api_deployment_id != normalized["api_deployment_id"]
                    or job.expected_worker_image_digest != normalized["backend_executor_digest"]
                ):
                    raise ValueError("Preview Provider generation job binding mismatch")
                attempt = await db.scalar(
                    select(GenerationAttempt)
                    .where(GenerationAttempt.id == UUID(normalized["attempt_id"]))
                    .with_for_update()
                )
                if (
                    attempt is None
                    or attempt.job_id != job.id
                    or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.PREPARED
                    or attempt.provider != "evolink"
                ):
                    raise ValueError("Preview Provider generation attempt is not prepared")
                asset = await db.scalar(
                    select(MediaAsset)
                    .where(MediaAsset.id == UUID(normalized["asset_id"]))
                    .with_for_update()
                )
                if (
                    asset is None
                    or asset.job_id != job.id
                    or MediaAssetRole(asset.role) is not MediaAssetRole.SOURCE
                    or MediaAssetStatus(asset.status) is not MediaAssetStatus.ACTIVE
                    or not is_provider_case_object_key(normalized, asset.object_key)
                    or asset.read_revoked_at is not None
                    or asset.expires_at <= current
                ):
                    raise ValueError("Preview Provider source asset is unavailable")
                issued = await create_provider_grant(
                    db,
                    asset=asset,
                    provider="evolink",
                    purpose="generation-input",
                    job_id=job.id,
                    attempt_id=attempt.id,
                    commit=False,
                    now=current,
                )
                result = build_grant_reference(normalized, issued)
        return result
    finally:
        await engine.dispose()


def _write_private_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-reference", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        reference = validate_input_reference(
            json.loads(Path(args.input_reference).read_text(encoding="utf-8"))
        )
        database_url = os.environ.get(args.database_url_env, "").strip()
        if not database_url:
            raise ValueError("Preview Provider database URL is required")
        result = asyncio.run(prepare_grant(database_url, reference))
        _write_private_create_once(Path(args.output), result)
        print(
            json.dumps(
                {
                    "state": "PREPARED",
                    "grant_id_hash": hashlib.sha256(result["grant_id"].encode()).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
