"""Authenticated Web API for Partner Invite consent workflows."""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.error_response import get_request_id
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.order import AcceptedOrder
from app.schemas.partner_invite import (
    PartnerInviteAcceptRequest,
    PartnerInviteConsentRequest,
    PartnerInviteCreateRead,
    PartnerInviteCreateRequest,
    PartnerInviteOrderRequest,
    PartnerInviteSnapshot,
    PartnerInviteVersionRequest,
    PartnerWithdrawalRead,
)
from app.services.feature_flag_service import require_request_capability
from app.services.partner_invite_service import (
    PartnerInviteError,
    accept_partner_invite,
    consent_partner_invite,
    create_partner_invite,
    create_partner_order,
    read_partner_invite,
    revoke_partner_invite,
    withdraw_partner_invite,
)


router = APIRouter(prefix="/partner-invites", tags=["partner-invites"])
settings = get_settings()


def _raise_partner_invite_error(exc: PartnerInviteError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": "The partner invitation request could not be completed.",
            "retryable": exc.status_code >= 500,
        },
    ) from exc


async def _require_partner_invite(
    request: Request,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    await require_request_capability(
        request,
        db,
        Capability.PARTNER_INVITE,
        verified_user_id=user_id,
    )


def _hmac_key() -> bytes:
    return settings.secret_key.encode("utf-8")


@router.post("", response_model=PartnerInviteCreateRead, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: PartnerInviteCreateRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerInviteCreateRead:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await create_partner_invite(
            db,
            host_user_id=current_user.id,
            hmac_key=_hmac_key(),
            frontend_base_url=settings.effective_frontend_base_url,
            template_id=payload.template_id,
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.post("/accept", response_model=PartnerInviteSnapshot)
async def accept_invite(
    payload: PartnerInviteAcceptRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerInviteSnapshot:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await accept_partner_invite(
            db,
            token=payload.token,
            partner_user_id=current_user.id,
            hmac_key=_hmac_key(),
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.get("/{invite_id}", response_model=PartnerInviteSnapshot)
async def get_invite(
    invite_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerInviteSnapshot:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await read_partner_invite(
            db,
            invite_id=invite_id,
            user_id=current_user.id,
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.post("/{invite_id}/consent", response_model=PartnerInviteSnapshot)
async def record_consent(
    invite_id: uuid.UUID,
    payload: PartnerInviteConsentRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerInviteSnapshot:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await consent_partner_invite(
            db,
            invite_id=invite_id,
            partner_user_id=current_user.id,
            expected_version=payload.expected_version,
            order_intent_id=payload.order_intent_id,
            order_intent_hash=payload.order_intent_hash,
            partner_asset_id=payload.partner_asset_id,
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.post(
    "/{invite_id}/order",
    response_model=AcceptedOrder,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_invite_order(
    invite_id: uuid.UUID,
    payload: PartnerInviteOrderRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> AcceptedOrder:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await create_partner_order(
            db,
            invite_id=invite_id,
            host_user_id=current_user.id,
            expected_version=payload.expected_version,
            host_asset_id=payload.host_asset_id,
            consent_event_id=payload.consent_event_id,
            idempotency_key=idempotency_key,
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.post("/{invite_id}/revoke", response_model=PartnerInviteSnapshot)
async def revoke_invite(
    invite_id: uuid.UUID,
    payload: PartnerInviteVersionRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerInviteSnapshot:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await revoke_partner_invite(
            db,
            invite_id=invite_id,
            host_user_id=current_user.id,
            expected_version=payload.expected_version,
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)


@router.post("/{invite_id}/withdraw", response_model=PartnerWithdrawalRead)
async def withdraw_invite(
    invite_id: uuid.UUID,
    payload: PartnerInviteVersionRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PartnerWithdrawalRead:
    await _require_partner_invite(request, db, current_user.id)
    try:
        return await withdraw_partner_invite(
            db,
            invite_id=invite_id,
            partner_user_id=current_user.id,
            expected_version=payload.expected_version,
            request_id=get_request_id(request),
        )
    except PartnerInviteError as exc:
        _raise_partner_invite_error(exc)
