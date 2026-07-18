"""Broker-verified Google PKCE exchange into a local Cookie session."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability, FeatureFlagState
from app.core.security_headers import require_request_origin
from app.core.supabase_auth import SupabaseAuthError, SupabaseUserClaims, verify_supabase_token
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.schemas.auth import OAuthIntentRequest, OAuthIntentResponse, SupabaseSessionRequest
from app.schemas.user import UserRead
from app.services.acceptance_identity_service import (
    compute_subject_hmac,
    consume_binding_row,
    lock_acceptance_binding,
)
from app.services.auth_session_service import issue_session
from app.services.email_service import is_disposable_email
from app.services.feature_flag_service import require_request_capability, resolve_request_capability
from app.services.oauth_intent_service import (
    INTENT_TTL,
    OAUTH_BROWSER_COOKIE,
    OAuthIntentError,
    clear_oauth_browser_cookie,
    consume_oauth_intent,
    create_oauth_intent,
    set_oauth_browser_cookie,
)
from app.routers.auth._shared import OAUTH_INTENT_DEVICE_LIMITER, OAUTH_INTENT_IP_LIMITER, settings


router = APIRouter()


def _capability_error(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "capability_disabled", "capability": "google_auth", "reason": reason},
    )


def _enforce_intent_rate_limit(request: Request) -> None:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "unknown")
    device_id = str(request.headers.get("x-device-id") or "").strip()[:128]
    if not device_id:
        device_id = str(request.headers.get("user-agent") or "unknown").strip()[:256]
    if OAUTH_INTENT_IP_LIMITER.is_limited(client_ip) or OAUTH_INTENT_DEVICE_LIMITER.is_limited(device_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "oauth_intent_rate_limited", "retryable": True},
        )


@router.post("/oauth-intents", response_model=OAuthIntentResponse)
async def create_google_oauth_intent(
    payload: OAuthIntentRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> OAuthIntentResponse:
    _enforce_intent_rate_limit(request)
    origin = await require_request_origin(request, db)
    decision = await resolve_request_capability(db, Capability.GOOGLE_AUTH)
    if not decision.allowed and decision.reason != "cohort_identity_missing":
        raise _capability_error(decision.reason)
    if not settings.supabase_oauth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "supabase_auth_not_configured"},
        )
    try:
        issued = await create_oauth_intent(db, redirect_path=payload.next_path)
    except OAuthIntentError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    set_oauth_browser_cookie(response, issued.browser_binding)
    return OAuthIntentResponse(
        intent_token=issued.token,
        callback_url=f"{origin}/pages/auth/callback",
        redirect_path=issued.row.redirect_path,
        expires_at=issued.row.expires_at,
    )


async def _locked_identity_user(
    db: AsyncSession,
    claims: SupabaseUserClaims,
) -> tuple[UserIdentity | None, User | None]:
    lock_key = f"vowpic.identity.supabase\0{claims.subject}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key})
    result = await db.execute(
        select(UserIdentity, User)
        .join(User, User.id == UserIdentity.user_id)
        .where(
            UserIdentity.provider == "supabase",
            UserIdentity.subject == claims.subject,
            UserIdentity.revoked_at.is_(None),
        )
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row is not None else (None, None)


def _ensure_active(user: User) -> None:
    if (user.status or "").strip().lower() != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "account_not_active"})


async def _create_identity_user(
    db: AsyncSession,
    claims: SupabaseUserClaims,
    *,
    now: datetime,
) -> tuple[UserIdentity, User]:
    if is_disposable_email(claims.email):
        raise HTTPException(status_code=422, detail={"code": "disposable_email_forbidden"})
    user = User(
        email=claims.email,
        email_verified_at=now,
        nickname=claims.nickname[:64] if claims.nickname else None,
        avatar_url=claims.avatar_url[:512] if claims.avatar_url else None,
        role="user",
        status="active",
        last_login_at=now,
    )
    db.add(user)
    await db.flush()
    identity = UserIdentity(
        user_id=user.id,
        provider="supabase",
        subject=claims.subject,
        verified_email_snapshot=claims.email,
    )
    db.add(identity)
    await db.flush()
    return identity, user


def _refresh_profile(identity: UserIdentity, user: User, claims: SupabaseUserClaims, now: datetime) -> None:
    _ensure_active(user)
    identity.verified_email_snapshot = claims.email
    user.email = claims.email
    user.email_verified_at = user.email_verified_at or now
    if claims.nickname:
        user.nickname = claims.nickname[:64]
    if claims.avatar_url:
        user.avatar_url = claims.avatar_url[:512]
    user.last_login_at = now


@router.post("/supabase/session", response_model=UserRead)
async def exchange_supabase_session(
    payload: SupabaseSessionRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> User:
    await require_request_origin(request, db)
    initial_decision = await resolve_request_capability(db, Capability.GOOGLE_AUTH)
    if (
        not initial_decision.allowed
        and initial_decision.reason != "cohort_identity_missing"
    ):
        raise _capability_error(initial_decision.reason)
    browser_binding = request.cookies.get(OAUTH_BROWSER_COOKIE) or ""
    try:
        await consume_oauth_intent(
            db,
            token=payload.intent_token,
            browser_binding=browser_binding,
        )
    except OAuthIntentError as exc:
        raise HTTPException(status_code=401, detail={"code": exc.code}) from exc
    try:
        claims = await verify_supabase_token(payload.access_token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail={"code": "supabase_session_invalid"}) from exc

    identity_hash = compute_subject_hmac(
        settings.acceptance_identity_hmac_key,
        "google",
        claims.subject,
    )
    identity, user = await _locked_identity_user(db, claims)
    decision = await require_request_capability(
        request,
        db,
        Capability.GOOGLE_AUTH,
        verified_user_id=user.id if user is not None else None,
        verified_identity_hash=identity_hash,
    )

    binding = None
    if decision.state is FeatureFlagState.ACCEPTANCE_COHORT:
        binding = await lock_acceptance_binding(
            db,
            provider="google",
            subject_hmac=identity_hash,
            environment=settings.runtime_environment,
            deployment_id=settings.deployment_id,
        )
        if binding is None:
            raise HTTPException(status_code=503, detail={"code": "acceptance_identity_binding_required"})

    current = datetime.now(timezone.utc)
    if user is None:
        identity, user = await _create_identity_user(db, claims, now=current)
    else:
        assert identity is not None
        _refresh_profile(identity, user, claims, current)
        await db.flush()

    if binding is not None and not await consume_binding_row(binding, user.id, now=current):
        raise HTTPException(status_code=409, detail={"code": "acceptance_identity_binding_reused"})
    await issue_session(
        db,
        user,
        response,
        identity_id=identity.id,
        now=current,
        acceptance_binding_id=binding.id if binding is not None else None,
    )
    await db.commit()
    await db.refresh(user)
    clear_oauth_browser_cookie(response)
    return user
