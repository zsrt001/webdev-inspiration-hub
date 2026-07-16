#!/usr/bin/env python3
"""Bind one exact all-OFF feature snapshot to a completed Preview Commercial runtime."""

from __future__ import annotations

import argparse
import asyncio
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

from app.core.database import normalize_database_url  # noqa: E402
from app.core.feature_flags import Capability  # noqa: E402
from app.models.ops_feature_flag import OpsFeatureFlag  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402


SNAPSHOT_FIELDS = (
    "capability", "environment", "state", "deployment_id", "runtime_bundle_id",
    "worker_image_digest", "release_activation_id", "target_manifest_sha256",
    "cohort_user_ids", "verified_identity_hashes", "expires_at", "version",
)
EXPECTED_CAPABILITIES = tuple(sorted(capability.value for capability in Capability))


def build_all_off_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if tuple(sorted(str(row.get("capability") or "") for row in rows)) != EXPECTED_CAPABILITIES:
        raise ValueError("Preview flag snapshot requires the exact capability set")
    normalized: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("capability") or "")):
        if set(row) != set(SNAPSHOT_FIELDS):
            raise ValueError("Preview flag snapshot row schema is invalid")
        if (
            row["environment"] != "preview"
            or row["state"] != "OFF"
            or any(
                row[field] is not None
                for field in (
                    "deployment_id", "runtime_bundle_id", "worker_image_digest",
                    "release_activation_id", "target_manifest_sha256", "expires_at",
                )
            )
            or list(row["cohort_user_ids"] or [])
            or list(row["verified_identity_hashes"] or [])
            or type(row["version"]) is not int
            or row["version"] < 1
        ):
            raise ValueError("Preview feature flags are not at the exact all-OFF baseline")
        normalized.append(
            {
                **row,
                "cohort_user_ids": [],
                "verified_identity_hashes": [],
            }
        )
    payload = {"schema": "vowpic.preview-all-off-snapshot.v1", "flags": normalized}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**payload, "snapshot_sha256": digest, "capability_count": len(normalized)}


def _flag_row(flag: OpsFeatureFlag) -> dict[str, Any]:
    return {
        "capability": flag.capability,
        "environment": flag.environment,
        "state": flag.state,
        "deployment_id": flag.deployment_id,
        "runtime_bundle_id": flag.runtime_bundle_id,
        "worker_image_digest": flag.worker_image_digest,
        "release_activation_id": (
            str(flag.release_activation_id) if flag.release_activation_id is not None else None
        ),
        "target_manifest_sha256": flag.target_manifest_sha256,
        "cohort_user_ids": list(flag.cohort_user_ids or []),
        "verified_identity_hashes": list(flag.verified_identity_hashes or []),
        "expires_at": flag.expires_at.isoformat() if flag.expires_at is not None else None,
        "version": int(flag.version),
    }


async def bind_all_off_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise ValueError("Preview control-plane database URL is required")
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            async with db.begin():
                activation = await db.scalar(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == UUID(args.activation_id))
                    .with_for_update()
                )
                expected = {
                    "environment": "preview",
                    "kind": "PREVIEW_COMMERCIAL",
                    "source_sha": args.source_sha,
                    "runtime_bundle_id": args.runtime_bundle_id,
                    "api_deployment_id": args.api_deployment_id,
                    "worker_deployment_id": args.worker_deployment_id,
                    "worker_image_digest": args.worker_image_digest,
                    "phase": "COMPLETED",
                }
                if activation is None or any(
                    str(getattr(activation, key)) != str(value) for key, value in expected.items()
                ):
                    raise ValueError("Preview Commercial activation coordinates mismatch")
                flags = list(
                    (
                        await db.scalars(
                            select(OpsFeatureFlag)
                            .where(OpsFeatureFlag.environment == "preview")
                            .order_by(OpsFeatureFlag.capability)
                            .with_for_update()
                        )
                    ).all()
                )
                snapshot = build_all_off_snapshot([_flag_row(flag) for flag in flags])
                digest = snapshot["snapshot_sha256"]
                existing = (activation.current_snapshot_hash, activation.target_snapshot_hash)
                if existing == (digest, digest):
                    state = "ALREADY_BOUND"
                elif existing != (None, None):
                    raise ValueError("Preview Commercial activation already owns another snapshot")
                else:
                    activation.current_snapshot_hash = digest
                    activation.target_snapshot_hash = digest
                    activation.version = int(activation.version) + 1
                    state = "BOUND"
        return {
            "schema": snapshot["schema"],
            "state": state,
            "activation_id": args.activation_id,
            "source_sha": args.source_sha,
            "runtime_bundle_id": args.runtime_bundle_id,
            "api_deployment_id": args.api_deployment_id,
            "worker_deployment_id": args.worker_deployment_id,
            "worker_image_digest": args.worker_image_digest,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "capability_count": snapshot["capability_count"],
        }
    finally:
        await engine.dispose()


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--api-deployment-id", required=True)
    parser.add_argument("--worker-deployment-id", required=True)
    parser.add_argument("--worker-image-digest", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
    parser.add_argument("--output", required=True)
    parser.add_argument("--job-env")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
            raise ValueError("Preview source SHA is invalid")
        result = asyncio.run(bind_all_off_snapshot(args))
        _write_create_once(Path(args.output), result)
        if args.job_env:
            with Path(args.job_env).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"PREVIEW_FEATURE_SNAPSHOT_SHA256={result['snapshot_sha256']}\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
