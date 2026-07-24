#!/usr/bin/env python3
"""Fail-closed cleanup for one protected website-backend Preview activation."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx


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
from app.models.account_risk_event import AccountRiskEvent  # noqa: E402
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.credit_purchase import CreditPurchase  # noqa: E402
from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType  # noqa: E402
from app.models.credit_transaction import CreditTransaction  # noqa: E402
from app.models.live_portrait_job import LivePortraitJob  # noqa: E402
from app.models.media_asset import MediaAsset, MediaAssetStatus  # noqa: E402
from app.models.ops_feature_flag import OpsFeatureFlag  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.models.subscription_credit_grant import SubscriptionCreditGrant  # noqa: E402
from app.models.user_credit import UserCredit  # noqa: E402
from app.models.user_subscription import UserSubscription  # noqa: E402
from app.models.welcome_grant_claim import WelcomeGrantClaim  # noqa: E402
from app.services.feature_flag_service import emergency_disable  # noqa: E402
from app.services.storage import DeleteResult, StorageService  # noqa: E402
from scripts.release.configure_preview_auth_origin import (  # noqa: E402
    _read_state,
    exact_https_origin,
    remove_owned_callback,
)


CLEANUP_TABLES = (
    "acceptance_identity_bindings",
    "auth_sessions",
    "auth_refresh_tokens",
    "ops_feature_flags",
    "ops_feature_flag_audits",
    "release_activations",
)
EXPECTED_BUSINESS_TABLES = (
    "user_credits",
    "credit_transactions",
    "credit_grant_lots",
    "welcome_grant_claims",
    "credit_purchases",
    "orders",
    "live_portrait_jobs",
    "user_subscriptions",
    "subscription_credit_grants",
    "account_risk_events",
)
BUSINESS_MODELS = (
    UserCredit,
    CreditTransaction,
    CreditGrantLot,
    WelcomeGrantClaim,
    CreditPurchase,
    Order,
    LivePortraitJob,
    UserSubscription,
    SubscriptionCreditGrant,
    AccountRiskEvent,
)
ALLOWED_ROLES = frozenset({"PREVIEW_IDENTITY", "PREVIEW_COMMERCIAL"})


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


async def _find_activation(db, args: argparse.Namespace, *, lock: bool) -> ReleaseActivation | None:
    query = select(ReleaseActivation).where(
        ReleaseActivation.environment == "preview",
        ReleaseActivation.kind == args.role,
        ReleaseActivation.source_sha == args.source_sha,
        ReleaseActivation.workflow_run_id == args.workflow_run_id,
        ReleaseActivation.workflow_attempt == args.workflow_attempt,
    )
    if lock:
        query = query.with_for_update()
    result = await db.execute(query)
    rows = list(result.scalars())
    if len(rows) > 1:
        raise ValueError("Preview cleanup activation lookup is ambiguous")
    return rows[0] if rows else None


def _validate_activation(activation: ReleaseActivation, args: argparse.Namespace) -> None:
    role = str(args.role or "").strip().upper()
    if role not in ALLOWED_ROLES or activation.kind != role:
        raise ValueError("Preview cleanup activation kind mismatch")
    expected_api_role = "PREVIEW_IDENTITY" if role == "PREVIEW_IDENTITY" else "PREVIEW_COMMERCIAL_API"
    if activation.api_role != expected_api_role:
        raise ValueError("Preview cleanup activation role mismatch")
    if role == "PREVIEW_COMMERCIAL":
        if any(
            getattr(activation, field) is not None
            for field in ("worker_role", "worker_image_digest", "worker_deployment_id")
        ):
            raise ValueError("Preview cleanup found retired external Worker coordinates")
    if activation.phase not in {"RESERVED", "DEPLOYED", "COMPLETED", "CLEANED"}:
        raise ValueError("Preview cleanup activation phase is invalid")
    if args.runtime_bundle_id and activation.runtime_bundle_id != args.runtime_bundle_id:
        raise ValueError("Preview cleanup runtime bundle mismatch")
    if bool(activation.current_snapshot_hash) != bool(activation.target_snapshot_hash):
        raise ValueError("Preview cleanup activation has an incomplete state snapshot")


def _origin_state_artifact_status(
    activation: ReleaseActivation,
    args: argparse.Namespace,
    *,
    project_ref: str,
    callback_url: str,
) -> str:
    if not args.origin_state or not Path(args.origin_state).is_file():
        return "ABSENT"
    try:
        state = _read_state(Path(args.origin_state))
    except (OSError, ValueError):
        return "INVALID_IGNORED"
    expected = {
        "activation_id": str(activation.id),
        "source_sha": activation.source_sha,
        "runtime_bundle_id": activation.runtime_bundle_id,
        "api_deployment_id": activation.api_deployment_id,
        "api_deployment_url": exact_https_origin(activation.api_deployment_url),
        "workflow_run_id": str(activation.workflow_run_id),
        "workflow_attempt": int(activation.workflow_attempt),
        "project_ref": project_ref,
        "callback_url": callback_url,
        "original_sha256": activation.current_snapshot_hash,
        "target_sha256": activation.target_snapshot_hash,
    }
    if any(str(state.get(key)) != str(value) for key, value in expected.items()):
        return "INVALID_IGNORED"
    return "VALID"


async def _make_safe(
    session_factory: async_sessionmaker,
    args: argparse.Namespace,
) -> tuple[ReleaseActivation | None, dict[str, Any], tuple[Any, ...]]:
    now = datetime.now(timezone.utc)
    counters = {"bindings_revoked": 0, "sessions_revoked": 0, "refresh_tokens_revoked": 0}
    async with session_factory() as db:
        async with db.begin():
            activation = await _find_activation(db, args, lock=True)
            if activation is None:
                return None, counters, ()
            _validate_activation(activation, args)

            for capability in Capability:
                await emergency_disable(
                    db,
                    capability,
                    environment="preview",
                    actor=args.actor,
                    reason=f"{args.reason}; activation={activation.id}",
                )

            consumed_binding_ids: list[Any] = []
            consumed_user_ids: list[Any] = []
            if args.role == "PREVIEW_IDENTITY" and activation.api_deployment_id:
                bindings_result = await db.execute(
                    select(AcceptanceIdentityBinding)
                    .where(
                        AcceptanceIdentityBinding.environment == "preview",
                        AcceptanceIdentityBinding.deployment_id == activation.api_deployment_id,
                    )
                    .with_for_update()
                )
                for binding in bindings_result.scalars():
                    if binding.consumed_user_id is not None:
                        consumed_binding_ids.append(binding.id)
                        consumed_user_ids.append(binding.consumed_user_id)
                    elif binding.revoked_at is None:
                        binding.revoked_at = now
                        counters["bindings_revoked"] += 1

            if consumed_binding_ids:
                sessions_result = await db.execute(
                    select(AuthSession)
                    .where(AuthSession.acceptance_binding_id.in_(consumed_binding_ids))
                    .with_for_update()
                )
                sessions = list(sessions_result.scalars())
                session_ids = [session.id for session in sessions]
                for session in sessions:
                    if session.revoked_at is None:
                        session.revoked_at = now
                        session.token_version = int(session.token_version) + 1
                        counters["sessions_revoked"] += 1
                if session_ids:
                    tokens_result = await db.execute(
                        select(AuthRefreshToken)
                        .where(AuthRefreshToken.session_id.in_(session_ids))
                        .with_for_update()
                    )
                    for token in tokens_result.scalars():
                        if str(getattr(token.status, "value", token.status)) != RefreshTokenStatus.REVOKED.value:
                            token.status = RefreshTokenStatus.REVOKED
                            token.revoked_at = now
                            counters["refresh_tokens_revoked"] += 1
        unique_user_ids = tuple(sorted(set(consumed_user_ids), key=str))
        return activation, {
            **counters,
            "consumed_users": len(unique_user_ids),
            "second_binding": len(unique_user_ids) == 2,
        }, unique_user_ids


async def _cleanup_private_assets(
    session_factory: async_sessionmaker,
    user_ids: tuple[Any, ...],
) -> dict[str, Any]:
    private_asset_prefix = tuple(f"users/{user_id}/uploads/" for user_id in user_ids)
    if not user_ids:
        return {
            "private_asset_prefix": [],
            "storage_objects_deleted": 0,
            "storage_objects_remaining": 0,
        }

    async with session_factory() as db:
        assets_result = await db.execute(
            select(MediaAsset).where(MediaAsset.owner_user_id.in_(user_ids))
        )
        assets = list(assets_result.scalars())
    object_store = StorageService()
    listed_object_keys: set[str] = set()
    for prefix in private_asset_prefix:
        listed_object_keys.update(
            await asyncio.to_thread(object_store.list_private, prefix, limit=1000)
        )
    object_keys = sorted(listed_object_keys | {asset.object_key for asset in assets})
    delete_results = {
        object_key: await asyncio.to_thread(object_store.delete_private, object_key)
        for object_key in object_keys
    }
    failed = sorted(
        object_key
        for object_key, result in delete_results.items()
        if result == DeleteResult.FAILED
    )
    if failed:
        raise ValueError("Preview private asset cleanup failed")

    remaining: list[str] = []
    for prefix in private_asset_prefix:
        remaining.extend(await asyncio.to_thread(object_store.list_private, prefix, limit=1000))
    if remaining:
        raise ValueError("Preview private storage prefix is not empty after cleanup")

    now = datetime.now(timezone.utc)
    if assets:
        asset_ids = [asset.id for asset in assets]
        async with session_factory() as db:
            async with db.begin():
                locked_result = await db.execute(
                    select(MediaAsset)
                    .where(MediaAsset.id.in_(asset_ids))
                    .with_for_update()
                )
                for asset in locked_result.scalars():
                    asset.status = MediaAssetStatus.DELETED
                    asset.deleted_at = asset.deleted_at or now
                    asset.read_revoked_at = asset.read_revoked_at or now
                    asset.deletion_reason = asset.deletion_reason or "preview_smoke_cleanup"
                    asset.deletion_blockers = []
                    asset.next_delete_at = None
                    asset.lease_owner = None
                    asset.lease_claim_id = None
                    asset.lease_expires_at = None
    return {
        "private_asset_prefix": list(private_asset_prefix),
        "storage_objects_deleted": sum(
            result == DeleteResult.DELETED for result in delete_results.values()
        ),
        "storage_objects_remaining": len(remaining),
    }


async def _verify_and_finalize(
    session_factory: async_sessionmaker,
    args: argparse.Namespace,
) -> dict[str, Any]:
    async with session_factory() as db:
        activation = await _find_activation(db, args, lock=False)
        if activation is None:
            return {"state": "NO_ACTIVATION", "business_rows": {}}
        _validate_activation(activation, args)
        if args.role == "PREVIEW_IDENTITY" and (
            activation.current_snapshot_hash or activation.target_snapshot_hash
        ):
            if not args.origin_restored:
                raise ValueError("Preview callback snapshot exists but restoration was not proven")

        flag_result = await db.execute(
            select(OpsFeatureFlag).where(OpsFeatureFlag.environment == "preview")
        )
        flags = list(flag_result.scalars())
        expected = {capability.value for capability in Capability}
        if {flag.capability for flag in flags} != expected:
            raise ValueError("Preview feature flag baseline is incomplete after cleanup")
        if any(
            flag.state != FeatureFlagState.OFF.value
            or flag.deployment_id is not None
            or flag.runtime_bundle_id is not None
            or flag.release_activation_id is not None
            or flag.expires_at is not None
            or list(flag.cohort_user_ids or [])
            or list(flag.verified_identity_hashes or [])
            for flag in flags
        ):
            raise ValueError("Preview feature flags are not fully OFF after cleanup")

        consumed_result = await db.execute(
            select(AcceptanceIdentityBinding.consumed_user_id).where(
                AcceptanceIdentityBinding.environment == "preview",
                AcceptanceIdentityBinding.deployment_id == activation.api_deployment_id,
                AcceptanceIdentityBinding.consumed_user_id.is_not(None),
            )
        )
        user_ids = sorted({row[0] for row in consumed_result.all()})
        business_rows: dict[str, int] = {}
        for table_name, model in zip(EXPECTED_BUSINESS_TABLES, BUSINESS_MODELS, strict=True):
            if user_ids:
                count_result = await db.execute(
                    select(func.count()).select_from(model).where(model.user_id.in_(user_ids))
                )
                count = int(count_result.scalar_one())
            else:
                count = 0
            business_rows[table_name] = count
        expected_rows = {
            "user_credits": len(user_ids),
            "credit_transactions": len(user_ids),
            "credit_grant_lots": len(user_ids),
            "welcome_grant_claims": len(user_ids),
            "credit_purchases": 0,
            "orders": 0,
            "live_portrait_jobs": 0,
            "user_subscriptions": 0,
            "subscription_credit_grants": 0,
            "account_risk_events": 0,
        }
        mismatches = {
            name: {"expected": expected_rows[name], "actual": count}
            for name, count in business_rows.items()
            if count != expected_rows[name]
        }
        if mismatches:
            raise ValueError(
                "Preview identity smoke financial lineage mismatch: "
                + ", ".join(
                    f"{name}={values['actual']} expected={values['expected']}"
                    for name, values in sorted(mismatches.items())
                )
            )
        for user_id in user_ids:
            credit = await db.scalar(
                select(UserCredit).where(UserCredit.user_id == user_id)
            )
            transaction = await db.scalar(
                select(CreditTransaction).where(CreditTransaction.user_id == user_id)
            )
            lot = await db.scalar(
                select(CreditGrantLot).where(CreditGrantLot.user_id == user_id)
            )
            claim = await db.scalar(
                select(WelcomeGrantClaim).where(WelcomeGrantClaim.user_id == user_id)
            )
            if (
                credit is None
                or int(credit.balance or 0) != 2
                or int(credit.reserved_balance or 0) != 0
                or transaction is None
                or str(getattr(transaction.transaction_type, "value", transaction.transaction_type))
                != "WELCOME_BONUS"
                or int(transaction.amount) != 2
                or transaction.root_transaction_id != transaction.id
                or lot is None
                or str(getattr(lot.source_type, "value", lot.source_type))
                != GrantLotSourceType.WELCOME.value
                or int(lot.original_amount) != 2
                or lot.root_transaction_id != transaction.id
                or claim is None
                or claim.user_identity_id is None
                or claim.credit_transaction_id != transaction.id
                or claim.grant_lot_id != lot.id
            ):
                raise ValueError("Preview identity welcome lineage is not exact")

    async with session_factory() as db:
        async with db.begin():
            activation = await _find_activation(db, args, lock=True)
            if activation is None:
                raise ValueError("Preview activation disappeared during cleanup")
            _validate_activation(activation, args)
            if activation.phase != "CLEANED":
                activation.phase = "CLEANED"
                activation.phase_rank = max(int(activation.phase_rank) + 1, 100)
                activation.version = int(activation.version) + 1
        activation_id = str(activation.id)
        source_sha = activation.source_sha
        runtime_bundle_id = activation.runtime_bundle_id
        api_deployment_id = activation.api_deployment_id
        worker_deployment_id = activation.worker_deployment_id
    return {
        "state": "CLEANED",
        "activation_id": activation_id,
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "api_deployment_id": api_deployment_id,
        "worker_deployment_id": worker_deployment_id,
        "business_rows": business_rows,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise ValueError("Preview cleanup database URL is required")
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            activation = await _find_activation(db, args, lock=False)
        origin_restored = False
        origin_cleanup: dict[str, Any] = {
            "state": "NOT_REQUIRED",
            "origin_state_artifact": "ABSENT",
        }
        if activation is not None:
            _validate_activation(activation, args)
            has_origin_snapshot = args.role == "PREVIEW_IDENTITY" and bool(
                activation.current_snapshot_hash or activation.target_snapshot_hash
            )
            if has_origin_snapshot:
                token = os.environ.get(args.management_token_env, "").strip()
                project_ref = os.environ.get(args.project_ref_env, "").strip().lower()
                origin = exact_https_origin(activation.api_deployment_url)
                callback_url = f"{origin}/pages/auth/callback"
                artifact_status = _origin_state_artifact_status(
                    activation,
                    args,
                    project_ref=project_ref,
                    callback_url=callback_url,
                )
                with httpx.Client(timeout=20.0) as client:
                    result = remove_owned_callback(
                        project_ref=project_ref,
                        callback_url=callback_url,
                        original_sha256=str(activation.current_snapshot_hash),
                        target_sha256=str(activation.target_snapshot_hash),
                        token=token,
                        client=client,
                    )
                origin_cleanup = {
                    **result,
                    "origin_state_artifact": artifact_status,
                }
                origin_restored = True
            elif args.origin_state and Path(args.origin_state).is_file():
                if args.role != "PREVIEW_IDENTITY":
                    raise ValueError("PREVIEW_COMMERCIAL cannot use an auth-origin state")
                origin_cleanup["origin_state_artifact"] = "UNREGISTERED_IGNORED"

        args.origin_restored = origin_restored
        safe_activation, counters, consumed_user_ids = await _make_safe(session_factory, args)
        if safe_activation is None:
            return {
                "state": "NO_ACTIVATION",
                **counters,
                "business_rows": {},
                "origin_cleanup": origin_cleanup,
            }
        private_cleanup = await _cleanup_private_assets(session_factory, consumed_user_ids)
        finalized = await _verify_and_finalize(session_factory, args)
        return {
            **finalized,
            **counters,
            **private_cleanup,
            "origin_restored": origin_restored,
            "origin_cleanup": origin_cleanup,
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ALLOWED_ROLES), default="PREVIEW_IDENTITY")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runtime-bundle-id", default="")
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--origin-state")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
    parser.add_argument("--management-token-env", default="SUPABASE_MANAGEMENT_TOKEN")
    parser.add_argument("--project-ref-env", default="SUPABASE_PROJECT_REF")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
