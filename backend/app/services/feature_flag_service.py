"""PostgreSQL-authoritative capability decisions and audited mutations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.feature_flags import (
    Capability,
    FeatureFlagContext,
    FeatureFlagDecision,
    FeatureFlagState,
)
from app.core.redis_client import (
    FEATURE_FLAG_OFF_CACHE_TTL_SECONDS,
    feature_flag_off_cache_key,
    get_redis,
)
from app.models.ops_feature_flag import OpsFeatureFlag
from app.models.ops_feature_flag_audit import OpsFeatureFlagAudit
from app.models.release_activation import ReleaseActivation
from app.services.acceptance_identity_service import has_unconsumed_acceptance_binding


settings = get_settings()
MAX_COHORT_TTL_SECONDS = 86400
IDENTITY_FOUNDATION_CAPABILITIES = frozenset(
    {Capability.GOOGLE_AUTH, Capability.AUTHENTICATED_UPLOAD, Capability.PRIVATE_DOWNLOAD}
)
GOOGLE_AUTH_ONLY_CAPABILITIES = frozenset({Capability.GOOGLE_AUTH})
GOOGLE_AUTH_ONLY_PHASES = frozenset({"ACCEPTANCE_READY"})
PRODUCTION_ACTIVATION_FENCE = "vowpic-production-capability-activation"


@dataclass(frozen=True)
class GoogleAuthOnlyAuthority:
    flag: OpsFeatureFlag
    activation: ReleaseActivation
    expires_at: datetime


class CapabilityDisabled(HTTPException):
    def __init__(self, capability: str, reason: str = "capability_disabled") -> None:
        super().__init__(
            status_code=503,
            detail={"code": "capability_disabled", "capability": capability, "reason": reason},
        )
        self.capability = capability
        self.reason = reason


def coerce_capability(value: Capability | str) -> Capability:
    if isinstance(value, Capability):
        return value
    try:
        return Capability(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown capability: {value}") from exc


def _row_value(row: object | dict[str, Any] | None, name: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, FeatureFlagState):
        return value.value
    if isinstance(value, Capability):
        return value.value
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _snapshot_hash(capability: Capability, row: object | dict[str, Any] | None) -> str:
    payload = {
        "capability": capability.value,
        "environment": _row_value(row, "environment"),
        "state": _row_value(row, "state", FeatureFlagState.OFF.value),
        "deployment_id": _row_value(row, "deployment_id"),
        "runtime_bundle_id": _row_value(row, "runtime_bundle_id"),
        "worker_image_digest": _row_value(row, "worker_image_digest"),
        "release_activation_id": _row_value(row, "release_activation_id"),
        "target_manifest_sha256": _row_value(row, "target_manifest_sha256"),
        "cohort_user_ids": sorted(str(item) for item in (_row_value(row, "cohort_user_ids", []) or [])),
        "verified_identity_hashes": sorted(
            str(item) for item in (_row_value(row, "verified_identity_hashes", []) or [])
        ),
        "expires_at": _row_value(row, "expires_at"),
        "version": _row_value(row, "version", 0),
    }
    canonical = json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _off_decision(capability: Capability, row: object | dict[str, Any] | None, reason: str) -> FeatureFlagDecision:
    return FeatureFlagDecision(
        capability=capability,
        state=FeatureFlagState.OFF,
        allowed=False,
        snapshot_hash=_snapshot_hash(capability, row),
        reason=reason,
    )


def validate_cohort_expiry(now: datetime, expires_at: datetime) -> None:
    if now.tzinfo is None or expires_at.tzinfo is None:
        raise ValueError("cohort timestamps must be timezone-aware")
    ttl = (expires_at - now).total_seconds()
    if ttl <= 0 or ttl > MAX_COHORT_TTL_SECONDS:
        raise ValueError("cohort expiry must be within 86400 seconds")


def decide_flag(
    capability: Capability | str,
    row: object | dict[str, Any] | None,
    context: FeatureFlagContext,
) -> FeatureFlagDecision:
    known_capability = coerce_capability(capability)
    if row is None:
        return _off_decision(known_capability, None, "flag_missing")
    try:
        state = FeatureFlagState(str(_row_value(row, "state", FeatureFlagState.OFF.value)))
    except ValueError:
        return _off_decision(known_capability, row, "state_invalid")
    if state is FeatureFlagState.OFF:
        return _off_decision(known_capability, row, "disabled")
    if context.environment not in {"preview", "production"}:
        return _off_decision(known_capability, row, "runtime_environment_invalid")
    if _row_value(row, "environment") != context.environment:
        return _off_decision(known_capability, row, "environment_mismatch")
    if not context.deployment_id or context.deployment_id != _row_value(row, "deployment_id"):
        return _off_decision(known_capability, row, "deployment_mismatch")
    if not context.runtime_bundle_id or context.runtime_bundle_id != _row_value(row, "runtime_bundle_id"):
        return _off_decision(known_capability, row, "runtime_bundle_mismatch")
    bound_worker = str(_row_value(row, "worker_image_digest") or "").strip()
    if bound_worker or context.worker_image_digest is not None:
        return _off_decision(known_capability, row, "worker_coordinate_retired")

    if state is FeatureFlagState.ACCEPTANCE_COHORT:
        expires_at = _row_value(row, "expires_at")
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at <= context.now:
            return _off_decision(known_capability, row, "cohort_expired")
        user_ids = {str(item) for item in (_row_value(row, "cohort_user_ids", []) or [])}
        identity_hashes = {
            str(item) for item in (_row_value(row, "verified_identity_hashes", []) or [])
        }
        has_user = context.user_id is not None and str(context.user_id) in user_ids
        has_identity = bool(
            context.verified_identity_hash and context.verified_identity_hash in identity_hashes
        )
        if not (has_user or has_identity):
            return _off_decision(known_capability, row, "cohort_identity_missing")

    return FeatureFlagDecision(
        capability=known_capability,
        state=state,
        allowed=True,
        snapshot_hash=_snapshot_hash(known_capability, row),
        reason="allowed",
    )


async def _load_authoritative_row(
    db: AsyncSession, environment: str, capability: Capability
) -> OpsFeatureFlag | None:
    result = await db.execute(
        select(OpsFeatureFlag).where(
            OpsFeatureFlag.environment == environment,
            OpsFeatureFlag.capability == capability.value,
        )
    )
    return result.scalar_one_or_none()


async def lock_google_auth_only_authority(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> GoogleAuthOnlyAuthority:
    """Serialize admission with activation cleanup and lock its exact authority."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Google acceptance timestamp must be timezone-aware")
    if (
        settings.runtime_environment != "production"
        or not settings.runtime_coordinates_valid
        or not settings.deployment_id
        or not settings.runtime_bundle_id.strip()
    ):
        raise CapabilityDisabled(Capability.GOOGLE_AUTH.value, "runtime_coordinates_invalid")
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": PRODUCTION_ACTIVATION_FENCE},
    )
    flag_result = await db.execute(
        select(OpsFeatureFlag)
        .where(
            OpsFeatureFlag.environment == "production",
            OpsFeatureFlag.capability == Capability.GOOGLE_AUTH.value,
        )
        .with_for_update()
    )
    flag = flag_result.scalar_one_or_none()
    if flag is None or flag.state != FeatureFlagState.ACCEPTANCE_COHORT.value:
        raise CapabilityDisabled(Capability.GOOGLE_AUTH.value, "disabled")
    if (
        flag.deployment_id != settings.deployment_id
        or flag.runtime_bundle_id != settings.runtime_bundle_id.strip()
        or flag.release_activation_id is None
        or flag.expires_at is None
        or flag.expires_at.tzinfo is None
        or flag.expires_at <= current
    ):
        raise CapabilityDisabled(Capability.GOOGLE_AUTH.value, "activation_coordinates_invalid")

    activations_result = await db.execute(
        select(ReleaseActivation)
        .where(
            ReleaseActivation.environment == "production",
            ReleaseActivation.kind == "GOOGLE_AUTH_ONLY",
            ReleaseActivation.api_deployment_id == settings.deployment_id,
            ReleaseActivation.phase != "CLEANED",
        )
        .with_for_update()
    )
    activations = list(activations_result.scalars().all())
    if len(activations) != 1 or activations[0].id != flag.release_activation_id:
        raise CapabilityDisabled(Capability.GOOGLE_AUTH.value, "activation_ambiguous")
    activation = activations[0]
    if (
        activation.phase not in GOOGLE_AUTH_ONLY_PHASES
        or activation.runtime_bundle_id != settings.runtime_bundle_id.strip()
        or activation.api_deployment_id != settings.deployment_id
        or activation.reservation_expires_at is None
        or activation.reservation_expires_at.tzinfo is None
        or activation.reservation_expires_at <= current
    ):
        raise CapabilityDisabled(Capability.GOOGLE_AUTH.value, "activation_expired")
    return GoogleAuthOnlyAuthority(
        flag=flag,
        activation=activation,
        expires_at=min(flag.expires_at, activation.reservation_expires_at),
    )


async def _with_verified_binding(
    db: AsyncSession,
    row: OpsFeatureFlag,
    context: FeatureFlagContext,
) -> OpsFeatureFlag | dict[str, Any]:
    if not context.verified_identity_hash:
        return row
    valid = False
    for provider in ("google", "google_email"):
        valid = await has_unconsumed_acceptance_binding(
            db,
            provider=provider,
            subject_hmac=context.verified_identity_hash,
            environment=context.environment,
            deployment_id=context.deployment_id or "",
            now=context.now,
        )
        if valid:
            break
    if not valid:
        return row
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
    } | {"verified_identity_hashes": [context.verified_identity_hash]}


async def _cache_off_decision(
    decision: FeatureFlagDecision,
    environment: str,
    *,
    ttl_seconds: int = FEATURE_FLAG_OFF_CACHE_TTL_SECONDS,
) -> None:
    if decision.state is not FeatureFlagState.OFF or decision.allowed:
        return
    try:
        redis = await get_redis()
        await asyncio.wait_for(
            redis.set(
                feature_flag_off_cache_key(environment, decision.capability.value),
                json.dumps({"snapshot_hash": decision.snapshot_hash, "reason": decision.reason}),
                ex=max(1, min(FEATURE_FLAG_OFF_CACHE_TTL_SECONDS, int(ttl_seconds))),
            ),
            timeout=0.5,
        )
    except Exception:
        # This cache can only make a decision more restrictive; PostgreSQL stays authoritative.
        return


async def _delete_cached_off(environment: str, capability: Capability) -> None:
    try:
        redis = await get_redis()
        await asyncio.wait_for(
            redis.delete(feature_flag_off_cache_key(environment, capability.value)),
            timeout=0.5,
        )
    except Exception:
        return


async def resolve_capability(
    db: AsyncSession,
    capability: Capability | str,
    context: FeatureFlagContext,
) -> FeatureFlagDecision:
    known_capability = coerce_capability(capability)
    if (
        context.environment not in {"preview", "production"}
        or not context.deployment_id
        or not context.runtime_bundle_id
    ):
        return _off_decision(known_capability, None, "runtime_coordinates_invalid")
    try:
        row = await _load_authoritative_row(db, context.environment, known_capability)
        if row is not None and str(_row_value(row, "state")) == FeatureFlagState.ACCEPTANCE_COHORT.value:
            row = await _with_verified_binding(db, row, context)
        decision = decide_flag(known_capability, row, context)
    except Exception:
        decision = _off_decision(known_capability, None, "authority_unavailable")
    if not decision.allowed:
        await _cache_off_decision(decision, context.environment, ttl_seconds=30)
    return decision


def _request_context(
    *,
    verified_user_id: UUID | None,
    verified_identity_hash: str | None,
) -> FeatureFlagContext:
    if not settings.runtime_coordinates_valid:
        return FeatureFlagContext(environment=settings.runtime_environment)
    return FeatureFlagContext(
        environment=settings.runtime_environment,
        deployment_id=settings.deployment_id,
        runtime_bundle_id=settings.runtime_bundle_id.strip(),
        user_id=verified_user_id,
        verified_identity_hash=verified_identity_hash,
    )


async def resolve_request_capability(
    db: AsyncSession,
    capability: Capability | str,
    *,
    verified_user_id: UUID | None = None,
    verified_identity_hash: str | None = None,
) -> FeatureFlagDecision:
    return await resolve_capability(
        db,
        capability,
        _request_context(
            verified_user_id=verified_user_id,
            verified_identity_hash=verified_identity_hash,
        ),
    )


async def require_request_capability(
    request: Request | None,
    db: AsyncSession,
    capability: Capability | str,
    *,
    verified_user_id: UUID | None = None,
    verified_identity_hash: str | None = None,
) -> FeatureFlagDecision:
    if verified_user_id is None and request is not None:
        request_user_id = str(getattr(request.state, "user_id", "") or "").strip()
        if request_user_id:
            try:
                verified_user_id = UUID(request_user_id)
            except ValueError:
                verified_user_id = None
    known_capability = coerce_capability(capability)
    try:
        decision = await resolve_request_capability(
            db,
            known_capability,
            verified_user_id=verified_user_id,
            verified_identity_hash=verified_identity_hash,
        )
    except Exception:
        decision = _off_decision(known_capability, None, "authority_unavailable")
    if not decision.allowed:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "capability_disabled",
                "capability": known_capability.value,
                "reason": decision.reason,
            },
        )
    return decision


async def require_backend_capability(
    db: AsyncSession,
    capability: Capability | str,
    *,
    deployment_id: str | None,
    runtime_bundle_id: str | None,
    user_id: UUID | None,
) -> FeatureFlagDecision:
    """Authorize persisted work only for the active website API deployment."""
    known_capability = coerce_capability(capability)
    required = (deployment_id, runtime_bundle_id, user_id)
    if any(value is None or not str(value).strip() for value in required):
        raise CapabilityDisabled(known_capability.value, "backend_stamp_missing")
    if not settings.runtime_coordinates_valid:
        raise CapabilityDisabled(known_capability.value, "backend_runtime_untrusted")
    if (
        deployment_id != settings.deployment_id
        or runtime_bundle_id != settings.runtime_bundle_id.strip()
    ):
        raise CapabilityDisabled(known_capability.value, "backend_stamp_mismatch")
    decision = await resolve_capability(
        db,
        known_capability,
        FeatureFlagContext(
            environment=settings.runtime_environment,
            deployment_id=deployment_id,
            runtime_bundle_id=runtime_bundle_id,
            user_id=user_id,
        ),
    )
    if not decision.allowed:
        raise CapabilityDisabled(known_capability.value, decision.reason)
    return decision


async def _validate_activation_for_state(
    db: AsyncSession,
    *,
    activation_id: UUID,
    environment: str,
    capability: Capability,
    deployment_id: str,
    runtime_bundle_id: str,
) -> ReleaseActivation:
    result = await db.execute(
        select(ReleaseActivation).where(ReleaseActivation.id == activation_id).with_for_update()
    )
    activation = result.scalar_one_or_none()
    if activation is None:
        raise ValueError("release activation does not exist")
    if activation.environment != environment:
        raise ValueError("release activation environment mismatch")
    if activation.runtime_bundle_id != runtime_bundle_id or activation.api_deployment_id != deployment_id:
        raise ValueError("release activation coordinates mismatch")
    if activation.phase in {"FAILED", "CLEANED"}:
        raise ValueError("release activation is not active")
    if activation.kind == "PREVIEW_IDENTITY" and capability not in IDENTITY_FOUNDATION_CAPABILITIES:
        raise ValueError("PREVIEW_IDENTITY cannot authorize this capability")
    if activation.kind == "GOOGLE_AUTH_ONLY":
        if activation.phase not in GOOGLE_AUTH_ONLY_PHASES:
            raise ValueError("GOOGLE_AUTH_ONLY activation is not acceptance-ready")
        if capability not in GOOGLE_AUTH_ONLY_CAPABILITIES:
            raise ValueError("GOOGLE_AUTH_ONLY cannot authorize this capability")
    return activation


async def set_capability_state(
    db: AsyncSession,
    capability: Capability | str,
    *,
    environment: str,
    state: FeatureFlagState | str,
    actor: str,
    reason: str,
    deployment_id: str | None = None,
    runtime_bundle_id: str | None = None,
    worker_image_digest: str | None = None,
    release_activation_id: UUID | None = None,
    target_manifest_sha256: str | None = None,
    cohort_user_ids: Iterable[UUID | str] = (),
    verified_identity_hashes: Iterable[str] = (),
    expires_at: datetime | None = None,
    now: datetime | None = None,
    allow_preview_enable: bool = False,
    allow_production_enable: bool = False,
) -> FeatureFlagDecision:
    known_capability = coerce_capability(capability)
    try:
        new_state = state if isinstance(state, FeatureFlagState) else FeatureFlagState(str(state))
    except ValueError as exc:
        raise ValueError(f"invalid feature flag state: {state}") from exc
    current = now or datetime.now(timezone.utc)
    clean_actor = actor.strip()
    clean_reason = reason.strip()
    if environment not in {"preview", "production"}:
        raise ValueError("environment must be preview or production")
    if not clean_actor or not clean_reason:
        raise ValueError("actor and reason are required")
    if worker_image_digest is not None:
        raise ValueError("worker image coordinates are retired")
    if environment == "production" and new_state is not FeatureFlagState.OFF and not allow_production_enable:
        raise ValueError("production capability mutation is OFF-only before commercial activation gates")
    if environment == "preview" and new_state is not FeatureFlagState.OFF and not allow_preview_enable:
        raise ValueError("preview enablement requires the later protected Preview workflow")
    if new_state is FeatureFlagState.ACCEPTANCE_COHORT:
        if expires_at is None:
            raise ValueError("acceptance cohort requires an expiry")
        validate_cohort_expiry(current, expires_at)
    elif expires_at is not None:
        raise ValueError("expiry is only valid for ACCEPTANCE_COHORT")
    if new_state is not FeatureFlagState.OFF:
        if not deployment_id or not runtime_bundle_id or release_activation_id is None:
            raise ValueError("non-OFF capability requires activation, deployment, and runtime bundle")
        await _validate_activation_for_state(
            db,
            activation_id=release_activation_id,
            environment=environment,
            capability=known_capability,
            deployment_id=deployment_id,
            runtime_bundle_id=runtime_bundle_id,
        )

    result = await db.execute(
        select(OpsFeatureFlag)
        .where(
            OpsFeatureFlag.environment == environment,
            OpsFeatureFlag.capability == known_capability.value,
        )
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = OpsFeatureFlag(
            environment=environment,
            capability=known_capability.value,
            state=FeatureFlagState.OFF.value,
            cohort_user_ids=[],
            verified_identity_hashes=[],
            version=1,
        )
        db.add(row)
        await db.flush()

    old_state = str(row.state)
    old_snapshot_hash = _snapshot_hash(known_capability, row)
    if new_state is FeatureFlagState.OFF:
        row.deployment_id = None
        row.runtime_bundle_id = None
        row.worker_image_digest = None
        row.release_activation_id = None
        row.target_manifest_sha256 = None
        row.cohort_user_ids = []
        row.verified_identity_hashes = []
        row.expires_at = None
    else:
        row.deployment_id = deployment_id
        row.runtime_bundle_id = runtime_bundle_id
        row.worker_image_digest = None
        row.release_activation_id = release_activation_id
        row.target_manifest_sha256 = target_manifest_sha256
        row.cohort_user_ids = sorted({str(item) for item in cohort_user_ids})
        row.verified_identity_hashes = sorted({str(item) for item in verified_identity_hashes})
        row.expires_at = expires_at
    row.state = new_state.value
    row.version = int(row.version or 0) + 1
    row.updated_at = current
    new_snapshot_hash = _snapshot_hash(known_capability, row)
    db.add(
        OpsFeatureFlagAudit(
            feature_flag_id=row.id,
            environment=environment,
            capability=known_capability.value,
            actor=clean_actor,
            reason=clean_reason,
            old_state=old_state,
            new_state=new_state.value,
            old_snapshot_hash=old_snapshot_hash,
            new_snapshot_hash=new_snapshot_hash,
            deployment_id=row.deployment_id,
            runtime_bundle_id=row.runtime_bundle_id,
            target_manifest_sha256=row.target_manifest_sha256,
            details_json={"version": row.version},
        )
    )
    await db.flush()
    await _delete_cached_off(environment, known_capability)
    return decide_flag(
        known_capability,
        row,
        FeatureFlagContext(
            environment=environment,
            deployment_id=row.deployment_id,
            runtime_bundle_id=row.runtime_bundle_id,
            user_id=next((UUID(value) for value in row.cohort_user_ids), None),
            verified_identity_hash=next(iter(row.verified_identity_hashes), None),
            now=current,
        ),
    )


async def emergency_disable(
    db: AsyncSession,
    capability: Capability | str,
    *,
    environment: str,
    actor: str,
    reason: str,
) -> FeatureFlagDecision:
    return await set_capability_state(
        db,
        capability,
        environment=environment,
        state=FeatureFlagState.OFF,
        actor=actor,
        reason=reason,
    )
