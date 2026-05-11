"""Admin-only authentication helpers."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.supabase_auth import (
    SupabaseAuthError,
    build_supabase_openid,
    verify_supabase_token,
)
from app.models.user import User
from app.services.schema_guard_service import ensure_user_account_columns

settings = get_settings()
ALGORITHM = "HS256"
ADMIN_ROLES = {"admin", "owner", "operator"}
OWNER_EMAILS = {"zst000001@gmail.com"}


def _extract_bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _is_configured_admin(user: User) -> bool:
    if (user.role or "").strip().lower() in ADMIN_ROLES:
        return True
    if str(user.id).lower() in settings.admin_user_id_list:
        return True
    if user.openid and user.openid in settings.admin_openid_list:
        return True
    if user.email and user.email.strip().lower() in settings.admin_email_list:
        return True
    if user.email and user.email.strip().lower() in OWNER_EMAILS:
        return True
    return False


async def _local_user_from_token(db: AsyncSession, token: str) -> User | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            return None
        user_id = uuid.UUID(str(sub))
    except (JWTError, ValueError, TypeError):
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _supabase_user_from_token(db: AsyncSession, token: str) -> User | None:
    try:
        claims = await verify_supabase_token(token)
    except SupabaseAuthError:
        return None

    result = await db.execute(
        select(User).where(
            or_(
                (User.auth_provider == "supabase") & (User.auth_subject == claims.subject),
                User.openid == build_supabase_openid(claims.subject),
                User.email == claims.email if claims.email else False,
            )
        )
    )
    user = result.scalar_one_or_none()
    if user is not None:
        user.email = user.email or claims.email
    return user


async def _admin_user_from_authorization(db: AsyncSession, authorization: str | None) -> User | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None
    await ensure_user_account_columns(db)
    return await _local_user_from_token(db, token) or await _supabase_user_from_token(db, token)


async def require_admin_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """
    Guard for admin-only endpoints.

    Preferred production path:
    - Frontend sends normal user JWT.
    - Backend checks user role or configured ADMIN_USER_IDS / ADMIN_EMAILS.

    `ADMIN_TOKEN` remains only for backend scripts and internal calls. It should
    never be stored in browser localStorage or embedded in frontend builds.
    """
    if settings.admin_token and x_admin_token and x_admin_token == settings.admin_token:
        request.state.admin_actor = "admin-token"
        return

    user = await _admin_user_from_authorization(db, authorization)
    if user and _is_configured_admin(user):
        request.state.admin_actor = f"admin-user:{user.id}"
        return

    if settings.debug and not settings.admin_token and not settings.admin_identity_configured:
        request.state.admin_actor = "debug-admin"
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
    )
