"""Operational readiness endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.runtime_checks import run_readiness_checks
from app.models.order import Order, OrderStatus
from app.services.generation_service import generation_service
from app.services.ops_alert_service import get_ops_alerts, push_critical_alerts
from app.services.ops_config_service import get_public_ops_config
from app.services.retention_service import cleanup_expired_orders, cleanup_expired_source_images
from app.worker_tasks import run_order_generation

router = APIRouter(prefix="/ops", tags=["ops"])
settings = get_settings()


def _require_cron_auth(authorization: str | None) -> None:
    token = settings.effective_cleanup_cron_token
    if not token:
        raise HTTPException(status_code=503, detail="cron token is not configured")
    if (authorization or "").strip() != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid cron token")


def _seconds_since_iso(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = value
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc).timestamp() - parsed.timestamp()
    except Exception:
        return None


@router.get("/readiness")
@router.get("/health")
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
@router.get("/config")
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
    _require_cron_auth(authorization)
    source_images = await cleanup_expired_source_images(db)
    generated_assets = await cleanup_expired_orders(db)
    return {"success": True, "source_images": source_images, "generated_assets": generated_assets}


@router.get("/check_alerts")
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


@router.get("/poll_pending_orders")
@router.post("/poll_pending_orders")
async def poll_pending_orders(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=12, ge=1, le=50),
    lookback_hours: int = Query(default=24, ge=1, le=168),
):
    """Cron-safe order status poller for launch validation mode."""
    _require_cron_auth(authorization)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    result = await db.execute(
        select(Order)
        .where(
            Order.deleted_at.is_(None),
            Order.status == OrderStatus.GENERATING,
            Order.updated_at >= cutoff,
        )
        .order_by(Order.updated_at.asc(), Order.created_at.asc())
        .limit(limit)
    )
    orders = result.scalars().all()
    summary = {
        "checked": 0,
        "refreshed": 0,
        "restarted_inline_background": 0,
        "still_generating": 0,
        "completed": 0,
        "failed": 0,
        "orders": [],
    }

    for order in orders:
        summary["checked"] += 1
        refreshed = await generation_service.refresh_order(str(order.id))
        if refreshed:
            summary["refreshed"] += 1
            await db.refresh(order)

        status_value = order.status.value if isinstance(order.status, OrderStatus) else str(order.status)
        order_entry = {"order_id": str(order.id), "status": status_value, "refreshed": refreshed}
        if status_value == OrderStatus.COMPLETED.value:
            summary["completed"] += 1
            summary["orders"].append(order_entry)
            continue
        if order.error_message:
            summary["failed"] += 1
            order_entry["error_message"] = str(order.error_message)[:300]
            summary["orders"].append(order_entry)
            continue

        params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
        if str(params.get("execution_mode") or "") == "inline_background":
            age_seconds = _seconds_since_iso(params.get("inline_background_started_at"))
            retry_count = int(params.get("inline_background_retry_count") or 0)
            if age_seconds is not None and age_seconds >= 360 and retry_count < 2:
                params["inline_background_retry_count"] = retry_count + 1
                params["inline_background_started_at"] = datetime.now(timezone.utc).isoformat()
                order.generation_params = params
                await db.commit()
                await db.refresh(order)
                background_tasks.add_task(run_order_generation, str(order.id))
                summary["restarted_inline_background"] += 1
                order_entry["restarted"] = True

        if order.status == OrderStatus.GENERATING:
            summary["still_generating"] += 1
        summary["orders"].append(order_entry)

    return {"success": True, **summary}
