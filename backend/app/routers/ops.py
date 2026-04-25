"""Operational readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.runtime_checks import run_readiness_checks
from app.services.ops_config_service import get_public_ops_config

router = APIRouter(prefix="/ops", tags=["ops"])


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
