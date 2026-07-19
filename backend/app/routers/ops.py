"""Operational readiness endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.runtime_checks import run_readiness_checks
from app.models.release_activation import ReleaseActivation
from app.services.acceptance_media_verification_service import (
    AcceptanceMediaVerificationError,
    verify_acceptance_media_absence,
)
from app.services.ops_alert_service import get_ops_alerts, push_critical_alerts
from app.services.ops_config_service import get_public_ops_config
from app.services.feature_flag_service import require_request_capability, resolve_request_capability
from app.services.media_deletion_service import run_deletion_cleanup
from app.services.retention_service import cleanup_expired_orders, cleanup_expired_source_images

router = APIRouter(prefix="/ops", tags=["ops"])
settings = get_settings()

_CLEANUP_EXECUTION_RELEASE_ROLES = frozenset(
    {
        "PREVIEW_IDENTITY",
        "PREVIEW_COMMERCIAL",
        "COMMERCIAL_7A",
        "CONTRACT_7B",
    }
)


class AcceptanceMediaAbsenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: uuid.UUID
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_bundle_id: str = Field(pattern=r"^rtb_[0-9a-f]{64}$")
    deployment_id: str = Field(min_length=1, max_length=160)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _require_cron_auth(authorization: str | None) -> None:
    token = settings.effective_cleanup_cron_token
    if not token:
        raise HTTPException(status_code=503, detail="cron token is not configured")
    if (authorization or "").strip() != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid cron token")


def _require_cleanup_execution_role() -> None:
    """Keep deletion paused for the safe baseline and invalid hosted runtimes."""
    environment = settings.runtime_environment.strip().lower()
    release_role = settings.release_role.strip()
    if (
        environment in {"preview", "production"}
        and release_role not in _CLEANUP_EXECUTION_RELEASE_ROLES
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cleanup_paused",
                "message": "Deletion is paused for this release role.",
                "retryable": True,
            },
        )


@router.get("/readiness")
@router.get("/health")
async def readiness(probe_storage: bool = False, probe_generation_queue: bool = False, strict: bool = True):
    effective_strict = True if not settings.debug else bool(strict)
    report = await run_readiness_checks(
        probe_storage=probe_storage,
        probe_generation_queue=probe_generation_queue,
        strict_mode=effective_strict,
    )
    if effective_strict and not report.get("commercial_ready", False):
        raise HTTPException(status_code=503, detail=report)
    return report


@router.get("/public_config")
@router.get("/config")
async def public_config(db: AsyncSession = Depends(get_db)):
    """Return sanitized operator-managed config for the storefront."""
    config = get_public_ops_config()
    capability_states: dict[str, bool] = {}
    for capability in Capability:
        decision = await resolve_request_capability(db, capability)
        capability_states[capability.value] = decision.allowed
    config["capabilities"] = capability_states
    auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    auth["google_oauth_enabled"] = bool(
        capability_states[Capability.GOOGLE_AUTH.value] and settings.supabase_oauth_enabled
    )
    config["auth"] = auth
    return config


@router.post("/cleanup_expired_assets")
async def cleanup_expired_assets(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Cron-safe durable cleanup endpoint. Requires a bearer cleanup token."""
    _require_cron_auth(authorization)
    _require_cleanup_execution_role()
    now = datetime.now(timezone.utc)
    source_summary = await cleanup_expired_source_images(db, now=now)
    order_summary = await cleanup_expired_orders(db, now=now)
    await db.commit()
    deletion_summary = await run_deletion_cleanup(
        db,
        lease_owner=f"cron:{uuid.uuid4()}",
        now=now,
    )
    return {
        "success": True,
        "source_images": source_summary,
        "orders": order_summary,
        "deletion": deletion_summary,
    }


@router.post("/verify_acceptance_media_absence")
async def verify_account_media_absence(
    payload: AcceptanceMediaAbsenceRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Prove physical private-object absence for one closed acceptance account."""

    _require_cron_auth(authorization)
    _require_cleanup_execution_role()
    if (
        settings.source_sha != payload.source_sha
        or settings.runtime_bundle_id.strip().lower() != payload.runtime_bundle_id
        or settings.deployment_id != payload.deployment_id
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "acceptance_release_binding_mismatch"},
        )
    activation_result = await db.execute(
        select(ReleaseActivation.id).where(
            ReleaseActivation.environment == "production",
            ReleaseActivation.kind == "COMMERCIAL_7A",
            ReleaseActivation.source_sha == payload.source_sha,
            ReleaseActivation.runtime_bundle_id == payload.runtime_bundle_id,
            ReleaseActivation.api_deployment_id == payload.deployment_id,
            ReleaseActivation.manifest_sha256 == payload.manifest_sha256,
        )
    )
    if activation_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "acceptance_activation_binding_missing"},
        )
    try:
        proof = await verify_acceptance_media_absence(
            db,
            user_id=payload.user_id,
        )
    except AcceptanceMediaVerificationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "acceptance_media_absence_not_proven"},
        ) from exc
    hmac_key = settings.acceptance_identity_hmac_key.encode("utf-8")
    if len(hmac_key) < 32:
        raise HTTPException(
            status_code=503,
            detail={"code": "acceptance_identity_hmac_unavailable"},
        )
    subject_hmac = hmac.new(
        hmac_key,
        str(payload.user_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "schema": "vowpic.acceptance-media-absence.v1",
        "passed": True,
        "source_sha": payload.source_sha,
        "runtime_bundle_id": payload.runtime_bundle_id,
        "deployment_id": payload.deployment_id,
        "manifest_sha256": payload.manifest_sha256,
        "user_subject_hmac_sha256": subject_hmac,
        "verified_asset_count": proof.verified_asset_count,
        "storage_read_outcome": "NOT_FOUND",
        "facts_sha256": proof.facts_sha256,
    }


@router.post("/check_alerts")
async def check_alerts(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Cron-safe alert check. Fetches alerts and pushes critical ones to webhook."""
    _require_cron_auth(authorization)
    alerts = await get_ops_alerts(db, days=1)
    push_result = await push_critical_alerts(alerts)
    return {"alerts": alerts, "push": push_result}


@router.post("/poll_pending_orders")
async def poll_pending_orders(
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Reject the retired inline/provider-polling execution path."""
    _require_cron_auth(authorization)
    raise HTTPException(
        status_code=410,
        detail={"code": "legacy_order_poller_retired", "replacement": "generation-job.v1"},
    )
