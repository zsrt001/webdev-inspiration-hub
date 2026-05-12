"""Orders API routes using database-backed state machine."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.user_auth import get_request_user
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import OrderCreate, OrderRead
from app.services.generation_service import generation_service
from app.services.order_creation_service import create_order_for_user
from app.services.retention_service import delete_storage_urls, order_asset_urls, user_has_paid_credit_history
from app.services.trial_access_service import (
    can_download_order,
)

router = APIRouter()


async def _serialize_order_for_user(db: AsyncSession, order: Order, user_id: uuid.UUID) -> OrderRead:
    has_paid_credits = await user_has_paid_credit_history(db, user_id)
    can_download = can_download_order(order.generation_params, has_paid_credits=has_paid_credits)
    payload = OrderRead.model_validate(order)
    payload.can_download = can_download
    payload.download_locked = not can_download
    if not can_download:
        payload.final_image_urls = None
    return payload


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
    for order in orders:
        if order.status == OrderStatus.GENERATING:
            await generation_service.refresh_order(str(order.id))
            await db.refresh(order)
    return [await _serialize_order_for_user(db, o, current_user.id) for o in orders]


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: str,
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
    if order.status == OrderStatus.GENERATING:
        await generation_service.refresh_order(str(order.id))
        await db.refresh(order)
    return await _serialize_order_for_user(db, order, current_user.id)


@router.post("/create", response_model=OrderRead)
@router.post("", response_model=OrderRead)
async def create_order(
    request: OrderCreate,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create order and enqueue generation.
    """
    order = await create_order_for_user(request, current_user, db)
    return await _serialize_order_for_user(db, order, current_user.id)


@router.delete("/{order_id}")
async def delete_my_order(
    order_id: str,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an order and remove associated stored image assets."""
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    result = await db.execute(select(Order).where(Order.id == order_uuid))
    order = result.scalar_one_or_none()
    if not order or order.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.deleted_at is None:
        summary = delete_storage_urls(order_asset_urls(order))
        order.source_image_urls = None
        order.preview_image_urls = None
        order.final_image_urls = None
        order.deleted_at = datetime.now(timezone.utc)
        order.storage_cleanup_status = "deleted" if summary["failed"] == 0 else "cleanup_failed"
        await db.flush()
    return {"success": True, "order_id": str(order.id), "storage_cleanup_status": order.storage_cleanup_status}
