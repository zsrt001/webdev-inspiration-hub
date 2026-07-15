"""Issue, rotate, revoke, and serialize local browser Cookie sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Iterable
import uuid

from fastapi import Request, Response
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security_headers import require_request_origin
from app.core.session_auth import (
    ACCESS_ALGORITHM,
    ACCESS_AUDIENCE,
    ACCESS_ISSUER,
    access_signing_key,
    validate_csrf_secret,
)
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.welcome_grant_service import ensure_welcome_grant_for_identity


ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
ACCESS_COOKIE = "vowpic_access"
REFRESH_COOKIE = "vowpic_refresh"
CSRF_COOKIE = "vowpic_csrf"


class SessionServiceError(Exception):
    def __init__(self, code: str, *, status_code: int = 401, clear_session: bool = False):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.clear_session = clear_session


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encode_access_token(
    session: AuthSession,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    issued_at = int(current.timestamp())
    expires_at = int((current + ACCESS_TTL).timestamp())
    payload = {
        "iss": ACCESS_ISSUER,
        "aud": ACCESS_AUDIENCE,
        "sub": str(user_id),
        "sid": str(session.id),
        "jti": str(uuid.uuid4()),
        "token_version": int(session.token_version),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, access_signing_key(), algorithm=ACCESS_ALGORITHM)


def set_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access_token, max_age=int(ACCESS_TTL.total_seconds()),
        path="/api/v1", secure=True, httponly=True, samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh_token, max_age=int(REFRESH_TTL.total_seconds()),
        path="/api/v1/auth/refresh", secure=True, httponly=True, samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf_token, max_age=int(REFRESH_TTL.total_seconds()),
        path="/", secure=True, httponly=False, samesite="lax",
    )


def clear_session_cookies(response: Response) -> None:
    for name, path, httponly, samesite in (
        (ACCESS_COOKIE, "/api/v1", True, "lax"),
        (REFRESH_COOKIE, "/api/v1/auth/refresh", True, "strict"),
        (CSRF_COOKIE, "/", False, "lax"),
    ):
        response.set_cookie(
            name, "", max_age=0, expires=0, path=path,
            secure=True, httponly=httponly, samesite=samesite,
        )


def _status(value: RefreshTokenStatus | str) -> str:
    return value.value if isinstance(value, RefreshTokenStatus) else str(value)


def apply_refresh_rotation(
    session: AuthSession,
    current_token: AuthRefreshToken,
    *,
    replacement_token_id: uuid.UUID,
    csrf_token_hash: str,
    now: datetime,
) -> None:
    if _status(current_token.status) != RefreshTokenStatus.ACTIVE.value:
        raise ValueError("only an active refresh token can rotate")
    current_token.status = RefreshTokenStatus.USED
    current_token.used_at = now
    current_token.replacement_token_id = replacement_token_id
    session.token_version = int(session.token_version) + 1
    session.csrf_token_hash = csrf_token_hash


def revoke_session_family(
    session: AuthSession,
    tokens: Iterable[AuthRefreshToken],
    *,
    now: datetime,
) -> None:
    if session.revoked_at is None:
        session.revoked_at = now
        session.token_version = int(session.token_version) + 1
    for token in tokens:
        if _status(token.status) != RefreshTokenStatus.REVOKED.value:
            token.status = RefreshTokenStatus.REVOKED
            token.revoked_at = now


async def issue_session(
    db: AsyncSession,
    user: User,
    response: Response,
    *,
    identity_id: uuid.UUID,
    now: datetime | None = None,
    acceptance_binding_id: uuid.UUID | None = None,
) -> AuthSession:
    current = now or datetime.now(timezone.utc)
    await ensure_welcome_grant_for_identity(
        db,
        identity_id=identity_id,
        now=current,
    )
    raw_refresh = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        acceptance_binding_id=acceptance_binding_id,
        family_id=uuid.uuid4(),
        token_version=1,
        csrf_token_hash=hash_secret(raw_csrf),
        expires_at=current + REFRESH_TTL,
    )
    db.add(session)
    await db.flush()
    db.add(
        AuthRefreshToken(
            session_id=session.id,
            generation=1,
            token_hash=hash_secret(raw_refresh),
            status=RefreshTokenStatus.ACTIVE,
            expires_at=current + REFRESH_TTL,
        )
    )
    await db.flush()
    set_session_cookies(
        response,
        access_token=encode_access_token(session, user.id, now=current),
        refresh_token=raw_refresh,
        csrf_token=raw_csrf,
    )
    return session


async def _locked_refresh_context(
    db: AsyncSession,
    raw_refresh: str,
) -> tuple[AuthRefreshToken, AuthSession, list[AuthRefreshToken], User]:
    token_result = await db.execute(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.token_hash == hash_secret(raw_refresh))
        .with_for_update()
    )
    token = token_result.scalar_one_or_none()
    if token is None:
        raise SessionServiceError("refresh_invalid", clear_session=True)
    session_result = await db.execute(
        select(AuthSession).where(AuthSession.id == token.session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise SessionServiceError("refresh_session_missing", clear_session=True)
    tokens_result = await db.execute(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.session_id == session.id)
        .order_by(AuthRefreshToken.generation.asc())
        .with_for_update()
    )
    tokens = list(tokens_result.scalars().all())
    user_result = await db.execute(select(User).where(User.id == session.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise SessionServiceError("refresh_user_missing", clear_session=True)
    return token, session, tokens, user


async def rotate_session(
    db: AsyncSession,
    request: Request,
    response: Response,
    *,
    now: datetime | None = None,
) -> User:
    """Rotate a refresh generation exactly once and invalidate the old access/CSRF pair."""

    await require_request_origin(request, db)
    raw_refresh = str(request.cookies.get(REFRESH_COOKIE) or "").strip()
    if not raw_refresh or len(raw_refresh) > 512:
        raise SessionServiceError("refresh_missing", clear_session=True)
    current = now or datetime.now(timezone.utc)
    token, session, family_tokens, user = await _locked_refresh_context(db, raw_refresh)

    if _status(token.status) != RefreshTokenStatus.ACTIVE.value:
        revoke_session_family(session, family_tokens, now=current)
        await db.flush()
        await db.commit()
        raise SessionServiceError("refresh_reuse_detected", clear_session=True)
    if token.expires_at <= current or session.expires_at <= current or session.revoked_at is not None:
        revoke_session_family(session, family_tokens, now=current)
        await db.flush()
        await db.commit()
        raise SessionServiceError("refresh_expired", clear_session=True)
    if (user.status or "").strip().lower() != "active":
        revoke_session_family(session, family_tokens, now=current)
        await db.flush()
        await db.commit()
        raise SessionServiceError("account_not_active", status_code=403, clear_session=True)
    try:
        validate_csrf_secret(
            session.csrf_token_hash,
            request.cookies.get(CSRF_COOKIE) or "",
            request.headers.get("x-csrf-token") or "",
        )
    except ValueError as exc:
        raise SessionServiceError("csrf_invalid", status_code=403) from exc

    raw_replacement = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    replacement = AuthRefreshToken(
        id=uuid.uuid4(),
        session_id=session.id,
        generation=int(token.generation) + 1,
        token_hash=hash_secret(raw_replacement),
        status=RefreshTokenStatus.ACTIVE,
        expires_at=session.expires_at,
    )
    db.add(replacement)
    apply_refresh_rotation(
        session,
        token,
        replacement_token_id=replacement.id,
        csrf_token_hash=hash_secret(raw_csrf),
        now=current,
    )
    await db.flush()
    await db.commit()
    set_session_cookies(
        response,
        access_token=encode_access_token(session, user.id, now=current),
        refresh_token=raw_replacement,
        csrf_token=raw_csrf,
    )
    return user


async def logout_session(
    db: AsyncSession,
    request: Request,
    response: Response,
    *,
    now: datetime | None = None,
) -> None:
    """Revoke the database family; clearing browser state alone is never logout."""

    await require_request_origin(request, db)
    raw_session_id = str(getattr(request.state, "auth_session_id", "") or "").strip()
    try:
        session_id = uuid.UUID(raw_session_id)
    except ValueError as exc:
        raise SessionServiceError("session_invalid", clear_session=True) from exc
    current = now or datetime.now(timezone.utc)
    session_result = await db.execute(
        select(AuthSession).where(AuthSession.id == session_id).with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise SessionServiceError("session_not_found", clear_session=True)
    tokens_result = await db.execute(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.session_id == session.id)
        .order_by(AuthRefreshToken.generation.asc())
        .with_for_update()
    )
    family_tokens = list(tokens_result.scalars().all())
    try:
        validate_csrf_secret(
            session.csrf_token_hash,
            request.cookies.get(CSRF_COOKIE) or "",
            request.headers.get("x-csrf-token") or "",
        )
    except ValueError as exc:
        raise SessionServiceError("csrf_invalid", status_code=403) from exc
    revoke_session_family(session, family_tokens, now=current)
    await db.flush()
    await db.commit()
    clear_session_cookies(response)
