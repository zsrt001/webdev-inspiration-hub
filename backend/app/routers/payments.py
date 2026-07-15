"""Hosted credit-pack payment routes backed only by signed server facts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.payment import (
    AcceptedPaymentEvent,
    CheckoutRedirect,
    CreditPackCheckoutRequest,
    CreditPackStatusResponse,
)
from app.services.creem_event_service import CreemEventError, ingest_verified_creem_event
from app.services.feature_flag_service import require_request_capability
from app.services.payment_service import PaymentError, payment_service


router = APIRouter()
settings = get_settings()


def _raise_payment_error(exc: PaymentError | CreemEventError) -> None:
    status_code = int(exc.status_code)
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": getattr(exc, "message", "Payment request failed."),
            "retryable": status_code >= 500,
        },
    )


@router.post("/checkout", response_model=CheckoutRedirect, status_code=201)
async def create_checkout(
    payload: CreditPackCheckoutRequest,
    http_request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutRedirect:
    await require_request_capability(
        http_request,
        db,
        Capability.CREDIT_PACK_CHECKOUT,
        verified_user_id=current_user.id,
    )
    try:
        return await payment_service.create_checkout(
            db,
            user=current_user,
            product_code=payload.product_code,
            idempotency_key=idempotency_key,
            return_url=payload.return_url,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)


@router.get("/status/{purchase_id}", response_model=CreditPackStatusResponse)
async def get_payment_status(
    purchase_id: str,
    checkout_id: str | None = Query(default=None),
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> CreditPackStatusResponse:
    try:
        return await payment_service.get_purchase_status(
            db,
            purchase_id=purchase_id,
            user_id=current_user.id,
            checkout_id=checkout_id,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)


@router.post("/webhook/creem", response_model=AcceptedPaymentEvent)
async def creem_webhook(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> AcceptedPaymentEvent:
    raw_body = await http_request.body()
    signature = http_request.headers.get("creem-signature")
    try:
        return await ingest_verified_creem_event(
            db,
            raw_body=raw_body,
            signature=signature,
            webhook_secret=str(settings.creem_webhook_secret or "").encode("utf-8"),
        )
    except CreemEventError as exc:
        _raise_payment_error(exc)


@router.post("/{purchase_id}/refund")
async def initiate_refund(
    purchase_id: str,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    del purchase_id, current_user, db
    try:
        await payment_service.initiate_refund()
    except PaymentError as exc:
        _raise_payment_error(exc)
    return {"accepted": True}
