"""Cookie-only local access-token validation and request-scoped user loading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
import jwt
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security_headers import SAFE_METHODS, require_request_origin
from app.models.auth_session import AuthSession
from app.models.user import User


ACCESS_COOKIE = "vowpic_access"
CSRF_COOKIE = "vowpic_csrf"
ACCESS_ISSUER = "vowpic-web"
ACCESS_AUDIENCE = "vowpic-browser"
ACCESS_ALGORITHM = "HS256"
ACCESS_MAX_SECONDS = 900
CLOCK_SKEW_SECONDS = 30
ACCESS_SIGNING_KEY_MIN_BYTES = 32


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID
    jwt_id: UUID
    token_version: int
    issued_at: int
    expires_at: int


def access_signing_key() -> str:
    key = str(get_settings().secret_key or "")
    if len(key.encode("utf-8")) < ACCESS_SIGNING_KEY_MIN_BYTES:
        raise ValueError("SECRET_KEY must contain at least 32 bytes")
    return key


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"access claim {field} must be an integer")
    return value


def _uuid(value: object, *, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"access claim {field} must be a UUID")
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"access claim {field} must be a UUID") from exc


def validate_access_claim_shape(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    verify_time: bool = True,
) -> AccessClaims:
    """Validate every revocation coordinate before any database lookup."""

    if not isinstance(payload, dict):
        raise ValueError("access claims must be an object")
    user_id = _uuid(payload.get("sub"), field="sub")
    session_id = _uuid(payload.get("sid"), field="sid")
    jwt_id = _uuid(payload.get("jti"), field="jti")
    token_version = _integer(payload.get("token_version"), field="token_version")
    issued_at = _integer(payload.get("iat"), field="iat")
    expires_at = _integer(payload.get("exp"), field="exp")
    if token_version < 1 or expires_at <= issued_at:
        raise ValueError("access claims contain an invalid version or lifetime")
    if expires_at - issued_at > ACCESS_MAX_SECONDS + CLOCK_SKEW_SECONDS:
        raise ValueError("access token lifetime exceeds the contract")
    if verify_time:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("current time must be timezone-aware")
        current_ts = int(current.timestamp())
        if issued_at > current_ts + CLOCK_SKEW_SECONDS or expires_at <= current_ts:
            raise ValueError("access token is expired or issued in the future")
    return AccessClaims(
        user_id=user_id,
        session_id=session_id,
        jwt_id=jwt_id,
        token_version=token_version,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def decode_access_token(token: str, *, now: datetime | None = None) -> AccessClaims:
    clean = str(token or "").strip()
    if not clean or len(clean) > 4096 or clean.count(".") != 2:
        raise ValueError("access token is missing or malformed")
    try:
        payload = jwt.decode(
            clean,
            access_signing_key(),
            algorithms=[ACCESS_ALGORITHM],
            issuer=ACCESS_ISSUER,
            audience=ACCESS_AUDIENCE,
            options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
    except PyJWTError as exc:
        raise ValueError("access token signature or registered claims are invalid") from exc
    return validate_access_claim_shape(payload, now=now)


def validate_csrf_secret(session_hash: str, cookie_secret: str, header_secret: str) -> None:
    cookie = str(cookie_secret or "")
    header = str(header_secret or "")
    if not cookie or not header or len(cookie) > 256 or len(header) > 256:
        raise ValueError("CSRF token is missing")
    if not hmac.compare_digest(cookie.encode("utf-8"), header.encode("utf-8")):
        raise ValueError("CSRF cookie and header differ")
    actual = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(session_hash or ""), actual):
        raise ValueError("CSRF token does not belong to the session")


def _active_user(user: User) -> bool:
    return (user.status or "").strip().lower() == "active"


async def _resolve_session_user(
    request: Request,
    db: AsyncSession,
    *,
    optional: bool,
) -> User | None:
    token = str(request.cookies.get(ACCESS_COOKIE) or "").strip()
    if not token:
        if optional:
            return None
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "session_missing"})
    try:
        claims = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "session_invalid"}) from exc

    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id == claims.session_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "session_not_found"})
    session, user = row
    now = datetime.now(timezone.utc)
    if (
        session.user_id != claims.user_id
        or int(session.token_version) != claims.token_version
        or session.revoked_at is not None
        or session.expires_at <= now
        or not _active_user(user)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "session_revoked"})

    if request.method.upper() not in SAFE_METHODS:
        await require_request_origin(request, db)
        try:
            validate_csrf_secret(
                session.csrf_token_hash,
                request.cookies.get(CSRF_COOKIE) or "",
                request.headers.get("x-csrf-token") or "",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "csrf_invalid", "message": "CSRF validation failed."},
            ) from exc
    request.state.auth_session_id = str(session.id)
    request.state.auth_jti = str(claims.jwt_id)
    request.state.user_id = str(user.id)
    return user


async def get_session_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await _resolve_session_user(request, db, optional=False)
    assert user is not None
    return user


async def get_optional_session_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    return await _resolve_session_user(request, db, optional=True)
