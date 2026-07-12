"""Strict Google-backed Admin control plane for audited capability flags."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import is_configured_admin_user
from app.core.database import get_control_plane_db, get_db
from app.core.feature_flags import FeatureFlagState
from app.core.supabase_auth import SupabaseAuthError, verify_supabase_token
from app.models.ops_feature_flag import OpsFeatureFlag
from app.models.user import User
from app.services.feature_flag_service import (
    coerce_capability,
    emergency_disable,
    set_capability_state,
)
from app.services.schema_guard_service import ensure_user_account_columns


router = APIRouter(prefix="/ops/admin", tags=["ops-admin"])


class FeatureFlagMutationRequest(BaseModel):
    state: FeatureFlagState
    reason: str = Field(min_length=3, max_length=512)
    deployment_id: str | None = Field(default=None, max_length=160)
    runtime_bundle_id: str | None = Field(default=None, max_length=80)
    worker_image_digest: str | None = Field(default=None, max_length=80)
    release_activation_id: UUID | None = None
    target_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cohort_user_ids: list[UUID] = Field(default_factory=list, max_length=100)
    expires_at: datetime | None = None


class EmergencyDisableRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=512)


def _bearer(authorization: str | None) -> str:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


async def require_google_database_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Reject backend tokens, local JWTs, spoofed headers, and debug fallback."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google Admin bearer required")
    try:
        claims = await verify_supabase_token(token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Admin session") from exc
    if claims.provider.strip().lower() != "google":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google-backed Admin required")
    await ensure_user_account_columns(db)
    result = await db.execute(
        select(User).where(
            User.auth_provider == "supabase",
            User.auth_subject == claims.subject,
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not is_configured_admin_user(user) or (user.status or "").lower() != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Database Admin role required")
    request.state.admin_actor = f"google-admin:{user.id}"
    return user


@router.get("/feature-flags")
async def list_feature_flags(
    environment: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_google_database_admin),
):
    if environment not in {"preview", "production"}:
        raise HTTPException(status_code=422, detail="environment must be preview or production")
    rows = (
        await db.execute(
            select(OpsFeatureFlag)
            .where(OpsFeatureFlag.environment == environment)
            .order_by(OpsFeatureFlag.capability.asc())
        )
    ).scalars().all()
    return {
        "environment": environment,
        "flags": [
            {
                "capability": row.capability,
                "state": row.state,
                "deployment_id": row.deployment_id,
                "runtime_bundle_id": row.runtime_bundle_id,
                "worker_image_digest": row.worker_image_digest,
                "release_activation_id": str(row.release_activation_id) if row.release_activation_id else None,
                "target_manifest_sha256": row.target_manifest_sha256,
                "cohort_user_count": len(row.cohort_user_ids or []),
                "verified_identity_count": len(row.verified_identity_hashes or []),
                "expires_at": row.expires_at,
                "version": row.version,
            }
            for row in rows
        ],
    }


@router.put("/feature-flags/{capability}")
async def mutate_feature_flag(
    capability: str,
    environment: str,
    payload: FeatureFlagMutationRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_plane_db),
    _: User = Depends(require_google_database_admin),
):
    known_capability = coerce_capability(capability)
    actor = str(getattr(request.state, "admin_actor", ""))
    try:
        decision = await set_capability_state(
            db,
            known_capability,
            environment=environment,
            state=payload.state,
            actor=actor,
            reason=payload.reason,
            deployment_id=payload.deployment_id,
            runtime_bundle_id=payload.runtime_bundle_id,
            worker_image_digest=payload.worker_image_digest,
            release_activation_id=payload.release_activation_id,
            target_manifest_sha256=payload.target_manifest_sha256,
            cohort_user_ids=payload.cohort_user_ids,
            expires_at=payload.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "capability": decision.capability.value,
        "state": decision.state.value,
        "allowed_on_this_deployment": decision.allowed,
        "snapshot_hash": decision.snapshot_hash,
        "reason": decision.reason,
    }


@router.post("/feature-flags/{capability}/emergency-disable")
async def emergency_disable_feature_flag(
    capability: str,
    environment: str,
    payload: EmergencyDisableRequest,
    request: Request,
    db: AsyncSession = Depends(get_control_plane_db),
    _: User = Depends(require_google_database_admin),
):
    try:
        decision = await emergency_disable(
            db,
            coerce_capability(capability),
            environment=environment,
            actor=str(getattr(request.state, "admin_actor", "")),
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "capability": decision.capability.value,
        "state": decision.state.value,
        "snapshot_hash": decision.snapshot_hash,
        "propagation": "postgresql_committed_then_off_cache_invalidated",
    }
