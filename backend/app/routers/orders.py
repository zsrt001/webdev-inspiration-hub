"""Orders API routes using database-backed state machine."""

import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.schemas.order import (
    AcceptedOrder,
    OrderCreate,
    OrderFundingRead,
    OrderRead,
    TrialUnlockRead,
    TrialUnlockRequest,
)
from app.services.order_creation_service import create_order_for_user
from app.services.feature_flag_service import require_request_capability, resolve_request_capability
from app.services.idempotency_service import IdempotencyConflict
from app.services.private_download_service import (
    PrivateDownloadError,
    project_order_assets,
    read_order_funding,
    resolve_private_download,
    unlock_trial_order,
)

router = APIRouter()


async def _private_download_allowed(db: AsyncSession) -> bool:
    private_download = await resolve_request_capability(db, Capability.PRIVATE_DOWNLOAD)
    return private_download.allowed


def _raise_private_download_error(exc: PrivateDownloadError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": "Private order access was denied."},
    ) from exc


async def _serialize_order_for_user(
    db: AsyncSession,
    order: Order,
    user_id: uuid.UUID,
    *,
    private_download_allowed: bool | None = None,
) -> OrderRead:
    payload = OrderRead.model_validate(order)
    if private_download_allowed is None:
        private_download_allowed = await _private_download_allowed(db)
    try:
        projection = await project_order_assets(
            db,
            order=order,
            private_download_allowed=private_download_allowed,
        )
    except PrivateDownloadError as exc:
        _raise_private_download_error(exc)
    payload.assets = list(projection.assets)
    payload.can_download = projection.can_download
    payload.entitlement_status = projection.entitlement_status
    payload.access_tier = projection.access_tier
    return payload


@router.get("", response_model=list[OrderRead])
@router.get("/", response_model=list[OrderRead])
async def list_orders(
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all orders for current user (newest first)."""
    result = await db.execute(
        select(Order)
        .where(Order.user_id == current_user.id, Order.deleted_at.is_(None))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    private_download_allowed = await _private_download_allowed(db)
    return [
        await _serialize_order_for_user(
            db,
            order,
            current_user.id,
            private_download_allowed=private_download_allowed,
        )
        for order in orders
    ]


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: str,
    current_user: User = Depends(get_session_user),
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
    private_download_allowed = await _private_download_allowed(db)
    return await _serialize_order_for_user(
        db,
        order,
        current_user.id,
        private_download_allowed=private_download_allowed,
    )


@router.get("/{order_id}/funding", response_model=OrderFundingRead)
async def get_order_funding(
    order_id: str,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> OrderFundingRead:
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid order ID") from exc
    try:
        return await read_order_funding(
            db, order_id=order_uuid, user_id=current_user.id
        )
    except PrivateDownloadError as exc:
        _raise_private_download_error(exc)


@router.post("/{order_id}/unlock", response_model=TrialUnlockRead)
async def unlock_trial_order_route(
    order_id: str,
    payload: TrialUnlockRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> TrialUnlockRead:
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid order ID") from exc
    await require_request_capability(
        request,
        db,
        Capability.PRIVATE_DOWNLOAD,
        verified_user_id=current_user.id,
    )
    try:
        return await unlock_trial_order(
            db,
            order_id=order_uuid,
            user_id=current_user.id,
            root_transaction_id=payload.root_transaction_id,
            idempotency_key=idempotency_key or "",
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": "Idempotency conflict."},
        ) from exc
    except (PrivateDownloadError, ValueError) as exc:
        if isinstance(exc, PrivateDownloadError):
            _raise_private_download_error(exc)
        raise HTTPException(
            status_code=400,
            detail={"code": "idempotency_key_invalid", "message": "Idempotency-Key is required."},
        ) from exc


@router.get("/{order_id}/assets/{asset_id}/download")
async def download_order_asset(
    order_id: str,
    asset_id: str,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        order_uuid = uuid.UUID(order_id)
        asset_uuid = uuid.UUID(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid order or asset ID") from exc
    await require_request_capability(
        request,
        db,
        Capability.PRIVATE_DOWNLOAD,
        verified_user_id=current_user.id,
    )
    try:
        result = await resolve_private_download(
            db,
            order_id=order_uuid,
            asset_id=asset_uuid,
            user_id=current_user.id,
        )
    except PrivateDownloadError as exc:
        _raise_private_download_error(exc)
    return Response(
        content=result.content,
        media_type=result.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


async def require_generation_before_order_identity(
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Require a revocable session before evaluating a user-bound generation cohort."""
    await require_request_capability(
        request, db, Capability.GENERATION, verified_user_id=current_user.id
    )


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
    response_model=AcceptedOrder,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_generation_before_order_identity)],
)
async def create_order(
    request: OrderCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> AcceptedOrder:
    """
    Create order and enqueue generation.
    """
    await require_request_capability(
        None, db, Capability.GENERATION, verified_user_id=current_user.id
    )
    return await create_order_for_user(
        request,
        current_user,
        db,
        idempotency_key=idempotency_key or "",
    )


@router.delete(
    "/{order_id}",
    dependencies=[Depends(require_order_cleanup_paused)],
)
async def delete_my_order(
    order_id: str,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
):
    """Keep all references until Task 11 installs durable deletion retries."""
    _ = order_id, current_user, db
    raise_order_cleanup_paused()
