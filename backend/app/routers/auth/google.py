"""Google OAuth (Supabase) endpoints — start flow, exchange session, user lifecycle."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import Capability, FeatureFlagState
from app.core.supabase_auth import (
    SupabaseAuthError,
    SupabaseUserClaims,
    build_supabase_openid,
    verify_supabase_token,
)
from app.models.user import User
from app.schemas.auth import LoginResponse, SupabaseSessionRequest
from app.services.credit_service import grant_welcome_bonus
from app.services.account_risk_service import record_account_risk_event
from app.services.email_service import is_disposable_email
from app.services.schema_guard_service import ensure_user_account_columns
from app.services.acceptance_identity_service import (
    compute_subject_hmac,
    consume_binding_row,
    lock_acceptance_binding,
)
from app.services.feature_flag_service import require_request_capability, resolve_request_capability
from app.routers.auth._shared import settings, NEW_ACCOUNT_DEVICE_LIMITER
from app.routers.auth._helpers import (
    _build_login_response,
    _ensure_user_active,
    _welcome_bonus_metadata,
    _oauth_return_url,
    _enforce_new_account_risk_limits_persistent,
)

router = APIRouter()


@router.get("/supabase/google/start")
async def start_supabase_google_login(
    next: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    await require_request_capability(None, db, Capability.GOOGLE_AUTH)
    if not settings.supabase_oauth_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase Auth is not fully configured")

    redirect_to = _oauth_return_url(next)
    query = urlencode({"provider": "google", "redirect_to": redirect_to})
    authorize_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/authorize?{query}"
    return RedirectResponse(authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/supabase/session", response_model=LoginResponse)
async def exchange_supabase_session(
    request: SupabaseSessionRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    preflight = await resolve_request_capability(db, Capability.GOOGLE_AUTH)
    if not preflight.allowed and preflight.reason != "cohort_identity_missing":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "capability_disabled",
                "capability": Capability.GOOGLE_AUTH.value,
                "reason": preflight.reason,
            },
        )
    try:
        claims = await verify_supabase_token(request.access_token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Supabase session") from exc

    binding = None
    if not preflight.allowed:
        identity_hash = compute_subject_hmac(
            settings.acceptance_identity_hmac_key,
            "google",
            claims.subject,
        )
        decision = await require_request_capability(
            http_request,
            db,
            Capability.GOOGLE_AUTH,
            verified_identity_hash=identity_hash,
        )
        if decision.state is not FeatureFlagState.ACCEPTANCE_COHORT:
            raise HTTPException(status_code=503, detail="acceptance identity state mismatch")
        binding = await lock_acceptance_binding(
            db,
            provider="google",
            subject_hmac=identity_hash,
            environment=settings.runtime_environment,
            deployment_id=settings.deployment_id,
        )
        if binding is None:
            raise HTTPException(status_code=503, detail="acceptance identity binding unavailable")

    await ensure_user_account_columns(db)
    is_new_user, user = await _get_or_create_supabase_user(db, claims, http_request)
    if binding is not None and not await consume_binding_row(binding, user.id):
        raise HTTPException(status_code=409, detail="acceptance identity binding already consumed")
    await db.refresh(user)

    bonus_granted = False
    if is_new_user:
        bonus_granted = await grant_welcome_bonus(db, user.id, metadata=_welcome_bonus_metadata(http_request, provider="google"))
    await record_account_risk_event(
        db, event_type="google_register_created" if is_new_user else "google_login",
        request=http_request, user=user, email=user.email, provider="google",
        metadata={"welcome_bonus_granted": bonus_granted},
    )

    return _build_login_response(user)


async def _get_or_create_supabase_user(db: AsyncSession, claims: SupabaseUserClaims, request: Request) -> tuple[bool, User]:
    is_new = False
    normalized_email = (claims.email or "").strip().lower() or None

    result = await db.execute(select(User).where(User.auth_provider == "supabase", User.auth_subject == claims.subject))
    user = result.scalar_one_or_none()

    if user is None:
        openid = build_supabase_openid(claims.subject)
        result = await db.execute(select(User).where(User.openid == openid))
        user = result.scalar_one_or_none()

    if user is None and normalized_email:
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()

    if user is None:
        if normalized_email and is_disposable_email(normalized_email):
            raise HTTPException(status_code=422, detail="Disposable email addresses are not allowed")
        await _enforce_new_account_risk_limits_persistent(db, request, email=normalized_email, provider="google")
        _enforce_new_account_risk_limits_from_claims(claims)
        user = User(openid=build_supabase_openid(claims.subject), auth_provider="supabase", auth_subject=claims.subject)
        db.add(user)
        is_new = True

    _ensure_user_active(user)
    user.auth_provider = "supabase"
    user.auth_subject = claims.subject
    user.email = normalized_email
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    if claims.nickname:
        user.nickname = claims.nickname[:64]
    if claims.avatar_url:
        user.avatar_url = claims.avatar_url[:512]
    if not user.role:
        user.role = "user"
    if not user.status:
        user.status = "active"
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    return is_new, user


def _enforce_new_account_risk_limits_from_claims(claims: SupabaseUserClaims) -> None:
    identity = claims.email or claims.subject
    key = f"supabase:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
    if NEW_ACCOUNT_DEVICE_LIMITER.is_limited(key):
        raise HTTPException(status_code=429, detail={
            "error": "new_account_rate_limited",
            "message": "Too many new accounts from this identity. Please try again later.",
        })
