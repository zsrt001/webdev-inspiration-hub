#!/usr/bin/env python3
"""Enable bounded Preview auth and private upload for two verified identities."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.core.feature_flags import Capability, FeatureFlagState  # noqa: E402
from app.models.acceptance_identity_binding import AcceptanceIdentityBinding  # noqa: E402
from app.models.ops_feature_flag import OpsFeatureFlag  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.services.feature_flag_service import set_capability_state  # noqa: E402


EXPECTED_CAPABILITIES = tuple(sorted(capability.value for capability in Capability))


def _write_create_once(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


async def _enable(args: argparse.Namespace) -> dict[str, object]:
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise ValueError("Preview control-plane database URL is required")
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    activation_id = UUID(args.activation_id)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=args.ttl_seconds)
    try:
        async with session_factory() as db:
            async with db.begin():
                activation_result = await db.execute(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == activation_id)
                    .with_for_update()
                )
                activation = activation_result.scalar_one_or_none()
                if (
                    activation is None
                    or activation.environment != "preview"
                    or activation.kind != "PREVIEW_IDENTITY"
                    or activation.api_role != "PREVIEW_IDENTITY"
                    or activation.phase != "COMPLETED"
                    or activation.source_sha != args.source_sha
                    or activation.runtime_bundle_id != args.runtime_bundle_id
                    or activation.api_deployment_id != args.deployment_id
                ):
                    raise ValueError("Preview activation coordinates are not eligible for identity smoke")
                if activation.reservation_expires_at is None or expires_at > activation.reservation_expires_at:
                    raise ValueError("Preview identity cohort exceeds the activation reservation")

                flags_result = await db.execute(
                    select(OpsFeatureFlag)
                    .where(OpsFeatureFlag.environment == "preview")
                    .order_by(OpsFeatureFlag.capability)
                    .with_for_update()
                )
                flags = list(flags_result.scalars())
                if tuple(flag.capability for flag in flags) != EXPECTED_CAPABILITIES:
                    raise ValueError("Preview feature flag baseline is incomplete")
                for flag in flags:
                    if (
                        flag.state != FeatureFlagState.OFF.value
                        or flag.deployment_id is not None
                        or flag.runtime_bundle_id is not None
                        or flag.release_activation_id is not None
                        or flag.expires_at is not None
                        or list(flag.cohort_user_ids or [])
                        or list(flag.verified_identity_hashes or [])
                    ):
                        raise ValueError("all Preview capability flags must be at the OFF baseline")

                bindings_result = await db.execute(
                    select(AcceptanceIdentityBinding)
                    .where(
                        AcceptanceIdentityBinding.environment == "preview",
                        AcceptanceIdentityBinding.deployment_id == args.deployment_id,
                        AcceptanceIdentityBinding.provider == "google",
                        AcceptanceIdentityBinding.consumed_at.is_(None),
                        AcceptanceIdentityBinding.revoked_at.is_(None),
                        AcceptanceIdentityBinding.expires_at >= expires_at,
                    )
                    .with_for_update()
                )
                bindings = list(bindings_result.scalars())
                if len(bindings) != 2:
                    raise ValueError("exactly two unconsumed deployment-bound Google identities are required")
                identity_hashes = tuple(sorted(binding.subject_hmac for binding in bindings))
                decisions = {}
                for capability in (Capability.GOOGLE_AUTH, Capability.AUTHENTICATED_UPLOAD):
                    decisions[capability.value] = await set_capability_state(
                        db,
                        capability,
                        environment="preview",
                        state=FeatureFlagState.ACCEPTANCE_COHORT,
                        actor=args.actor,
                        reason=args.reason,
                        deployment_id=args.deployment_id,
                        runtime_bundle_id=args.runtime_bundle_id,
                        release_activation_id=activation_id,
                        verified_identity_hashes=identity_hashes,
                        expires_at=expires_at,
                        now=now,
                        allow_preview_enable=True,
                    )
        return {
            "state": "ACCEPTANCE_COHORT",
            "capabilities": [
                Capability.GOOGLE_AUTH.value,
                Capability.AUTHENTICATED_UPLOAD.value,
            ],
            "identity_count": 2,
            "activation_id": str(activation_id),
            "deployment_id": args.deployment_id,
            "runtime_bundle_id": args.runtime_bundle_id,
            "expires_at": expires_at.isoformat(),
            "snapshot_sha256": {
                capability: decision.snapshot_hash
                for capability, decision in sorted(decisions.items())
            },
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=5400, choices=range(1, 7201))
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(_enable(args))
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
