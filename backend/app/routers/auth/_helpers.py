"""Shared helper functions and IP/device utilities."""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.auth import LoginResponse
from app.services.account_risk_service import check_new_account_risk_limits, record_account_risk_event
from app.routers.auth._shared import ALGORITHM, DEFAULT_OAUTH_RETURN_PATH, settings, NEW_ACCOUNT_IP_LIMITER, NEW_ACCOUNT_DEVICE_LIMITER


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def _build_login_response(user: User) -> LoginResponse:
    access_token = create_access_token(
        data={"sub": str(user.id), "openid": user.openid, "auth_provider": user.auth_provider or "supabase", "username": user.username}
    )
    return LoginResponse(access_token=access_token, token_type="bearer", openid=user.openid, user_id=user.id, username=user.username)


def _ensure_user_active(user: User) -> None:
    status_value = (user.status or "active").strip().lower()
    if status_value not in {"active", ""}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "error": "account_not_active", "message": "This account is not active. Please contact support.", "status": status_value,
        })


def _welcome_bonus_metadata(request: Request | None, *, provider: str) -> dict:
    if request is None:
        return {"provider": provider}
    return {
        "provider": provider,
        "ip_hash": hashlib.sha256(_client_ip(request).encode("utf-8")).hexdigest()[:16],
        "device_hash": hashlib.sha256(_device_key(request).encode("utf-8")).hexdigest()[:16],
        "policy": "starter_single_generation_only",
    }


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _device_key(request: Request) -> str:
    explicit = (request.headers.get("x-device-id") or request.headers.get("x-visitor-id") or request.headers.get("x-client-fingerprint") or "").strip()
    if explicit:
        return explicit[:128]
    user_agent = (request.headers.get("user-agent") or "unknown")[:256]
    return hashlib.sha256(f"{_client_ip(request)}:{user_agent}".encode("utf-8")).hexdigest()


def _oauth_return_url(next_path: str | None) -> str:
    path = (next_path or DEFAULT_OAUTH_RETURN_PATH).strip()
    if not path.startswith("/") or path.startswith("//"):
        path = DEFAULT_OAUTH_RETURN_PATH
    if path.startswith("/api/") or path.startswith("/auth/"):
        path = DEFAULT_OAUTH_RETURN_PATH
    return f"{settings.effective_frontend_base_url.rstrip('/')}{path}"


def _enforce_new_account_risk_limits(request: Request) -> None:
    client_ip = _client_ip(request)
    device_key = _device_key(request)
    if NEW_ACCOUNT_IP_LIMITER.is_limited(client_ip) or NEW_ACCOUNT_DEVICE_LIMITER.is_limited(device_key):
        raise HTTPException(status_code=429, detail={
            "error": "new_account_rate_limited",
            "message": "Too many new accounts from this device or network. Please try again later.",
        })


async def _enforce_new_account_risk_limits_persistent(
    db: AsyncSession, request: Request, *, email: str | None, provider: str,
) -> None:
    try:
        _enforce_new_account_risk_limits(request)
    except HTTPException:
        await record_account_risk_event(db, event_type="new_account_blocked_memory_rate", request=request, email=email, provider=provider, risk_score=75)
        raise

    limit_hit = await check_new_account_risk_limits(db, request=request, ip_limit=settings.new_account_ip_limit_per_hour, device_limit=settings.new_account_device_limit_per_hour)
    if limit_hit:
        await record_account_risk_event(db, event_type=f"new_account_blocked_{limit_hit['scope']}_rate", request=request, email=email, provider=provider, risk_score=75, metadata=limit_hit)
        raise HTTPException(status_code=429, detail={
            "error": "new_account_rate_limited",
            "message": "Too many new accounts from this device or network. Please try again later.",
            **limit_hit,
        })
