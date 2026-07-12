"""Request-scoped user identity helpers."""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.supabase_auth import (
    SupabaseAuthError,
    SupabaseUserClaims,
    verify_supabase_token,
)
from app.models.user import User
from app.services.schema_guard_service import ensure_user_account_columns

settings = get_settings()
ALGORITHM = "HS256"
async def _get_user_by_sub(db: AsyncSession, sub: str) -> User | None:
    try:
        user_id = uuid.UUID(str(sub))
    except Exception:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _get_user_by_supabase_claims(
    db: AsyncSession,
    claims: SupabaseUserClaims,
) -> User | None:
    """Resolve only an identity already provisioned by the guarded exchange route."""
    result = await db.execute(
        select(User).where(
            User.auth_provider == "supabase",
            User.auth_subject == claims.subject,
        )
    )
    return result.scalar_one_or_none()


async def get_request_user(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Resolve one authenticated Web user; legacy caller-selected IDs are ignored."""
    token_error: str | None = None

    def _raise_unauthorized(detail: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization:
        _raise_unauthorized("missing_identity")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        _raise_unauthorized("invalid_authorization_header")

    clean_token = token.strip()
    try:
        payload = jwt.decode(clean_token, settings.secret_key, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            token_error = "token_missing_subject"
        else:
            await ensure_user_account_columns(db)
            user = await _get_user_by_sub(db, str(sub))
            if user:
                return user
            token_error = "token_user_not_found"
    except JWTError:
        token_error = "invalid_token"

    try:
        claims = await verify_supabase_token(clean_token)
        await ensure_user_account_columns(db)
        user = await _get_user_by_supabase_claims(db, claims)
        if user is None:
            _raise_unauthorized("token_user_not_found")
        return user
    except SupabaseAuthError:
        _raise_unauthorized(token_error or "invalid_token")
