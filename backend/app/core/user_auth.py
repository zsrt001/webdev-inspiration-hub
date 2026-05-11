"""Request-scoped user identity helpers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.supabase_auth import (
    SupabaseAuthError,
    SupabaseUserClaims,
    build_supabase_openid,
    verify_supabase_token,
)
from app.models.user import User

settings = get_settings()
ALGORITHM = "HS256"
MAX_OPENID_LENGTH = 64


def _normalize_openid(identity: str) -> str:
    value = (identity or "").strip()
    if not value:
        return ""
    if len(value) <= MAX_OPENID_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"u_{digest[: MAX_OPENID_LENGTH - 2]}"


async def _get_user_by_sub(db: AsyncSession, sub: str) -> User | None:
    try:
        user_id = uuid.UUID(str(sub))
    except Exception:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _get_or_create_user_by_openid(db: AsyncSession, openid: str) -> User:
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(openid=openid)
    db.add(user)
    await db.flush()
    return user


async def _get_or_create_user_by_supabase_claims(db: AsyncSession, claims: SupabaseUserClaims) -> User:
    normalized_email = (claims.email or "").strip().lower() or None
    result = await db.execute(
        select(User).where(
            User.auth_provider == "supabase",
            User.auth_subject == claims.subject,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        openid = build_supabase_openid(claims.subject)
        result = await db.execute(select(User).where(User.openid == openid))
        user = result.scalar_one_or_none()
    if user is None and normalized_email:
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
    if user is None:
        user = User(
            openid=build_supabase_openid(claims.subject),
            auth_provider="supabase",
            auth_subject=claims.subject,
        )
        db.add(user)

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
    return user


async def get_request_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_openid: str | None = Header(default=None, alias="X-User-OpenID"),
    x_visitor_id: str | None = Header(default=None, alias="X-Visitor-Id"),
) -> User:
    """Resolve request user from JWT first, then deterministic visitor identity."""
    token_error: str | None = None

    def _raise_unauthorized(detail: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            token_error = "invalid_authorization_header"
        else:
            try:
                payload = jwt.decode(token.strip(), settings.secret_key, algorithms=[ALGORITHM])
                sub = payload.get("sub")
                if not sub:
                    token_error = "token_missing_subject"
                else:
                    user = await _get_user_by_sub(db, str(sub))
                    if user:
                        return user
                    token_error = "token_user_not_found"
            except JWTError:
                token_error = "invalid_token"
            if token_error:
                try:
                    claims = await verify_supabase_token(token.strip())
                    return await _get_or_create_user_by_supabase_claims(db, claims)
                except SupabaseAuthError:
                    pass
        _raise_unauthorized(token_error or "invalid_token")

    raw_identity = (x_user_openid or x_visitor_id or "").strip()
    if not raw_identity:
        if settings.debug:
            client_ip = request.client.host if request.client else "unknown"
            user_agent = request.headers.get("user-agent", "")
            fingerprint = hashlib.sha256(f"{client_ip}|{user_agent}".encode("utf-8")).hexdigest()[:24]
            raw_identity = f"anon_{fingerprint}"
        else:
            _raise_unauthorized("missing_identity")

    normalized_identity = _normalize_openid(raw_identity)
    if not normalized_identity:
        _raise_unauthorized(token_error or "missing_identity")

    if x_user_openid:
        openid = normalized_identity
    else:
        openid = f"visitor_{normalized_identity}"
        if len(openid) > MAX_OPENID_LENGTH:
            openid = _normalize_openid(openid)

    return await _get_or_create_user_by_openid(db, openid)
