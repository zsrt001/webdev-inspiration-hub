"""Operational readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.runtime_checks import run_readiness_checks
from app.services.ops_config_service import get_public_ops_config
from app.services.retention_service import cleanup_expired_orders, cleanup_expired_source_images

router = APIRouter(prefix="/ops", tags=["ops"])
settings = get_settings()


@router.get("/readiness")
async def readiness(probe_storage: bool = False, probe_generation_queue: bool = False, strict: bool = True):
    report = await run_readiness_checks(
        probe_storage=probe_storage,
        probe_generation_queue=probe_generation_queue,
        strict_mode=strict,
    )
    if strict and not report.get("commercial_ready", False):
        raise HTTPException(status_code=503, detail=report)
    return report


@router.get("/public_config")
async def public_config():
    """Return sanitized operator-managed config for the storefront."""
    return get_public_ops_config()


@router.get("/cleanup_expired_assets")
@router.post("/cleanup_expired_assets")
async def cleanup_expired_assets(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Cron-safe cleanup endpoint. Requires a bearer cleanup token."""
    token = settings.effective_cleanup_cron_token
    if not token:
        raise HTTPException(status_code=503, detail="cleanup cron is not configured")
    if (authorization or "").strip() != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid cleanup token")
    source_images = await cleanup_expired_source_images(db)
    generated_assets = await cleanup_expired_orders(db)
    return {"success": True, "source_images": source_images, "generated_assets": generated_assets}
