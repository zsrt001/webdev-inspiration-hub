"""Orders API routes using database-backed state machine."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.user_auth import get_request_user
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import OrderCreate, OrderRead
from app.services.delivery_asset_service import build_download_variants, pick_master_image_url
from app.services.generation_service import generation_service
from app.services.order_creation_service import create_order_for_user
from app.services.feature_flag_service import require_request_capability, resolve_request_capability
from app.services.retention_service import user_has_paid_credit_history
from app.services.trial_access_service import (
    can_download_order,
)
from app.worker_tasks import run_order_generation

router = APIRouter()


def _seconds_since_iso(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc).timestamp() - parsed.timestamp()
    except Exception:
        return None


async def _restart_stale_inline_background(
    db: AsyncSession,
    order: Order,
    background_tasks: BackgroundTasks,
) -> bool:
    params = dict(order.generation_params) if isinstance(order.generation_params, dict) else {}
    if str(params.get("execution_mode") or "") != "inline_background":
        return False
    age_seconds = _seconds_since_iso(params.get("inline_background_started_at"))
    if age_seconds is None or age_seconds < 360:
        return False
    retry_count = int(params.get("inline_background_retry_count") or 0)
    if retry_count >= 2:
        return False
    params["inline_background_retry_count"] = retry_count + 1
    params["inline_background_started_at"] = datetime.now(timezone.utc).isoformat()
    order.generation_params = params
    await db.commit()
    await db.refresh(order)
    background_tasks.add_task(run_order_generation, str(order.id))
    return True


async def _read_capabilities(db: AsyncSession) -> tuple[bool, bool]:
    generation = await resolve_request_capability(db, Capability.GENERATION)
    private_download = await resolve_request_capability(db, Capability.PRIVATE_DOWNLOAD)
    return generation.allowed, private_download.allowed


async def _serialize_order_for_user(
    db: AsyncSession,
    order: Order,
    user_id: uuid.UUID,
    *,
    generation_allowed: bool | None = None,
    private_download_allowed: bool | None = None,
) -> OrderRead:
    payload = OrderRead.model_validate(order)
    if generation_allowed is None or private_download_allowed is None:
        generation_allowed, private_download_allowed = await _read_capabilities(db)
    if not (generation_allowed and private_download_allowed):
        payload.source_image_urls = None
        payload.preview_image_urls = None
        payload.final_image_urls = None
        payload.preview_master_image_url = None
        payload.final_master_image_url = None
        payload.download_variants = []
        payload.can_download = False
        payload.download_locked = True
        return payload

    has_paid_credits = await user_has_paid_credit_history(db, user_id)
    can_download = can_download_order(order.generation_params, has_paid_credits=has_paid_credits)
    payload.can_download = can_download
    payload.download_locked = not can_download
    payload.preview_master_image_url = pick_master_image_url(order.preview_image_urls)
    if not can_download:
        payload.final_image_urls = None
        payload.final_master_image_url = None
        payload.download_variants = []
    else:
        payload.final_master_image_url = pick_master_image_url(order.final_image_urls)
        payload.download_variants = build_download_variants(order.final_image_urls)
    return payload


@router.get("", response_model=list[OrderRead])
@router.get("/", response_model=list[OrderRead])
async def list_orders(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all orders for current user (newest first)."""
    result = await db.execute(
        select(Order)
        .where(Order.user_id == current_user.id, Order.deleted_at.is_(None))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    generation_allowed, private_download_allowed = await _read_capabilities(db)
    for order in orders:
        if generation_allowed and order.status == OrderStatus.GENERATING:
            await generation_service.refresh_order(str(order.id))
            await db.refresh(order)
    return [
        await _serialize_order_for_user(
            db,
            order,
            current_user.id,
            generation_allowed=generation_allowed,
            private_download_allowed=private_download_allowed,
        )
        for order in orders
    ]


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific order by ID."""
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    result = await db.execute(select(Order).where(Order.id == order_uuid))
    order = result.scalar_one_or_none()
    if not order or order.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    generation_allowed, private_download_allowed = await _read_capabilities(db)
    if generation_allowed and order.status == OrderStatus.GENERATING:
        restarted = await _restart_stale_inline_background(db, order, background_tasks)
        if not restarted:
            await generation_service.refresh_order(str(order.id))
            await db.refresh(order)
    return await _serialize_order_for_user(
        db,
        order,
        current_user.id,
        generation_allowed=generation_allowed,
        private_download_allowed=private_download_allowed,
    )


async def require_generation_before_order_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Fail closed before an order route resolves any user identity."""
    await require_request_capability(request, db, Capability.GENERATION)


def raise_order_cleanup_paused() -> None:
    """Keep all order references until durable deletion retries exist."""
    raise HTTPException(
        status_code=503,
        detail={
            "code": "cleanup_paused",
            "message": "Order deletion is paused until durable cleanup retries are available.",
            "retryable": True,
        },
    )


def require_order_cleanup_paused() -> None:
    """Stop HTTP dependency resolution before legacy identity dependencies run."""
    raise_order_cleanup_paused()


@router.post(
    "/create",
    response_model=OrderRead,
    dependencies=[Depends(require_generation_before_order_identity)],
)
async def create_order(
    request: OrderCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create order and enqueue generation.
    """
    await require_request_capability(None, db, Capability.GENERATION)
    order = await create_order_for_user(request, current_user, db, background_tasks=background_tasks)
    private_download = await resolve_request_capability(db, Capability.PRIVATE_DOWNLOAD)
    return await _serialize_order_for_user(
        db,
        order,
        current_user.id,
        generation_allowed=True,
        private_download_allowed=private_download.allowed,
    )


@router.delete(
    "/{order_id}",
    dependencies=[Depends(require_order_cleanup_paused)],
)
async def delete_my_order(
    order_id: str,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Keep all references until Task 11 installs durable deletion retries."""
    _ = order_id, current_user, db
    raise_order_cleanup_paused()
