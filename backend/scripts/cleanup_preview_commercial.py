#!/usr/bin/env python3
"""Fail-closed cleanup for a completed Preview Commercial acceptance run."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.core.feature_flags import Capability, FeatureFlagState  # noqa: E402
from app.models.acceptance_identity_binding import AcceptanceIdentityBinding  # noqa: E402
from app.models.account_tombstone import AccountTombstone  # noqa: E402
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.generation_job import GenerationJob  # noqa: E402
from app.models.ops_feature_flag import OpsFeatureFlag  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.services.feature_flag_service import emergency_disable  # noqa: E402
from scripts.release.cleanup_preview_identity_smoke import (  # noqa: E402
    _cleanup_private_assets,
)


def _validate_activation(activation: ReleaseActivation, args: argparse.Namespace) -> None:
    if (
        activation.environment != "preview"
        or activation.kind != "PREVIEW_COMMERCIAL"
        or activation.api_role != "PREVIEW_COMMERCIAL_API"
        or activation.source_sha != args.source_sha
        or activation.runtime_bundle_id != args.runtime_bundle_id
        or activation.api_deployment_id != args.deployment_id
        or str(activation.workflow_run_id) != args.workflow_run_id
        or int(activation.workflow_attempt) != args.workflow_attempt
        or activation.phase not in {"COMPLETED", "CLEANED"}
        or any(
            getattr(activation, field) is not None
            for field in (
                "worker_role",
                "worker_image_digest",
                "worker_deployment_id",
            )
        )
    ):
        raise ValueError("Preview Commercial cleanup coordinates are invalid")


def _validate_expected_identity_count(value: int) -> int:
    if value not in {0, 2}:
        raise ValueError("Preview Commercial cleanup expects either zero or two identities")
    return value


async def cleanup(args: argparse.Namespace) -> dict[str, object]:
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise ValueError("Preview migration database URL is required")
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    activation_id = UUID(args.activation_id)
    expected_identity_count = _validate_expected_identity_count(
        args.expected_consumed_identities
    )
    now = datetime.now(timezone.utc)
    try:
        async with session_factory() as db:
            async with db.begin():
                activation = await db.scalar(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == activation_id)
                    .with_for_update()
                )
                if activation is None:
                    raise ValueError("Preview Commercial activation does not exist")
                _validate_activation(activation, args)
                for capability in Capability:
                    await emergency_disable(
                        db,
                        capability,
                        environment="preview",
                        actor=args.actor,
                        reason=f"{args.reason}; activation={activation.id}",
                    )
                binding_result = await db.execute(
                    select(AcceptanceIdentityBinding)
                    .where(
                        AcceptanceIdentityBinding.environment == "preview",
                        AcceptanceIdentityBinding.deployment_id
                        == args.deployment_id,
                    )
                    .with_for_update()
                )
                bindings = list(binding_result.scalars())
                if len(bindings) != expected_identity_count:
                    raise ValueError(
                        "Preview Commercial cleanup identity binding count mismatch"
                    )
                user_ids = tuple(
                    sorted(
                        {
                            binding.consumed_user_id
                            for binding in bindings
                            if binding.consumed_user_id is not None
                        },
                        key=str,
                    )
                )
                if len(user_ids) != expected_identity_count:
                    raise ValueError(
                        "Preview Commercial consumed identity count mismatch"
                    )
                for binding in bindings:
                    if binding.consumed_user_id is None and binding.revoked_at is None:
                        binding.revoked_at = now
                sessions = []
                if bindings:
                    session_result = await db.execute(
                        select(AuthSession)
                        .where(
                            AuthSession.acceptance_binding_id.in_(
                                [binding.id for binding in bindings]
                            )
                        )
                        .with_for_update()
                    )
                    sessions = list(session_result.scalars())
                session_ids = [session.id for session in sessions]
                for session in sessions:
                    if session.revoked_at is None:
                        session.revoked_at = now
                        session.token_version = int(session.token_version) + 1
                if session_ids:
                    token_result = await db.execute(
                        select(AuthRefreshToken)
                        .where(AuthRefreshToken.session_id.in_(session_ids))
                        .with_for_update()
                    )
                    for token in token_result.scalars():
                        if (
                            str(getattr(token.status, "value", token.status))
                            != RefreshTokenStatus.REVOKED.value
                        ):
                            token.status = RefreshTokenStatus.REVOKED
                            token.revoked_at = now

        private_cleanup = await _cleanup_private_assets(session_factory, user_ids)

        async with session_factory() as db:
            tombstones = int(
                await db.scalar(
                    select(func.count())
                    .select_from(AccountTombstone)
                    .where(AccountTombstone.user_id.in_(user_ids))
                )
                or 0
            )
            active_sessions = int(
                await db.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(
                        AuthSession.user_id.in_(user_ids),
                        AuthSession.revoked_at.is_(None),
                    )
                )
                or 0
            )
            nonterminal_jobs = int(
                await db.scalar(
                    select(func.count())
                    .select_from(GenerationJob)
                    .join(Order, Order.id == GenerationJob.order_id)
                    .where(
                        Order.user_id.in_(user_ids),
                        GenerationJob.status.in_(
                            ("QUEUED", "ACTIVE", "RECONCILING")
                        ),
                    )
                )
                or 0
            )
            flags = list(
                (
                    await db.execute(
                        select(OpsFeatureFlag).where(
                            OpsFeatureFlag.environment == "preview"
                        )
                    )
                ).scalars()
            )
            expected_flags = {capability.value for capability in Capability}
            if (
                tombstones != expected_identity_count
                or active_sessions != 0
                or nonterminal_jobs != 0
                or {flag.capability for flag in flags} != expected_flags
                or any(
                    flag.state != FeatureFlagState.OFF.value
                    or flag.deployment_id is not None
                    or flag.runtime_bundle_id is not None
                    or flag.release_activation_id is not None
                    or flag.expires_at is not None
                    or list(flag.cohort_user_ids or [])
                    or list(flag.verified_identity_hashes or [])
                    for flag in flags
                )
            ):
                raise ValueError(
                    "Preview Commercial cleanup did not reach the safe terminal state"
                )

        async with session_factory() as db:
            async with db.begin():
                activation = await db.scalar(
                    select(ReleaseActivation)
                    .where(ReleaseActivation.id == activation_id)
                    .with_for_update()
                )
                if activation is None:
                    raise ValueError("Preview Commercial activation disappeared")
                _validate_activation(activation, args)
                if activation.phase != "CLEANED":
                    activation.phase = "CLEANED"
                    activation.phase_rank = max(int(activation.phase_rank) + 1, 100)
                    activation.version = int(activation.version) + 1
        return {
            "state": "CLEANED",
            "activation_id": str(activation_id),
            "source_sha": args.source_sha,
            "runtime_bundle_id": args.runtime_bundle_id,
            "api_deployment_id": args.deployment_id,
            "worker_deployment_id": None,
            "identity_count": expected_identity_count,
            "closed_identity_count": tombstones,
            "active_session_count": active_sessions,
            "nonterminal_generation_job_count": nonterminal_jobs,
            "storage_objects_remaining": private_cleanup["storage_objects_remaining"],
            "immutable_ledger_preserved": True,
        }
    finally:
        await engine.dispose()


def _write_create_once(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--expected-consumed-identities",
        type=int,
        choices=(0, 2),
        default=2,
    )
    parser.add_argument(
        "--database-url-env",
        default="PREVIEW_MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(cleanup(args))
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
