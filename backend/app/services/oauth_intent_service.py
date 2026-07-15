"""Browser-bound, single-use local intent for the Supabase PKCE exchange."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_login_intent import OAuthLoginIntent


INTENT_TTL = timedelta(minutes=10)
OAUTH_BROWSER_COOKIE = "vowpic_oauth_browser"
OAUTH_BROWSER_COOKIE_PATH = "/api/v1/auth"


class OAuthIntentError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IssuedOAuthIntent:
    row: OAuthLoginIntent
    token: str
    browser_binding: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_redirect_path(value: str | None) -> str:
    path = str(value or "/pages/account/index").strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or path.startswith("/api/")
        or path == "/api"
        or path.startswith("/auth/")
        or path == "/auth"
        or len(path) > 512
    ):
        raise OAuthIntentError("oauth_redirect_invalid")
    return path


def validate_intent_row(
    row: Any,
    *,
    token: str,
    browser_binding: str,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    if row is None:
        raise OAuthIntentError("oauth_intent_invalid")
    if row.consumed_at is not None:
        raise OAuthIntentError("oauth_intent_reused")
    if row.expires_at <= current:
        raise OAuthIntentError("oauth_intent_expired")
    token_hash = _hash(str(token or ""))
    browser_hash = _hash(str(browser_binding or ""))
    if not hmac.compare_digest(row.token_hash, token_hash):
        raise OAuthIntentError("oauth_intent_invalid")
    if not hmac.compare_digest(row.browser_binding_hash, browser_hash):
        raise OAuthIntentError("oauth_intent_wrong_browser")


async def create_oauth_intent(
    db: AsyncSession,
    *,
    redirect_path: str | None,
    now: datetime | None = None,
) -> IssuedOAuthIntent:
    current = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    browser_binding = secrets.token_urlsafe(32)
    row = OAuthLoginIntent(
        token_hash=_hash(token),
        browser_binding_hash=_hash(browser_binding),
        redirect_path=validate_redirect_path(redirect_path),
        expires_at=current + INTENT_TTL,
    )
    db.add(row)
    await db.flush()
    return IssuedOAuthIntent(row=row, token=token, browser_binding=browser_binding)


async def consume_oauth_intent(
    db: AsyncSession,
    *,
    token: str,
    browser_binding: str,
    now: datetime | None = None,
) -> OAuthLoginIntent:
    current = now or datetime.now(timezone.utc)
    token_hash = _hash(str(token or ""))
    result = await db.execute(
        select(OAuthLoginIntent)
        .where(OAuthLoginIntent.token_hash == token_hash)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    validate_intent_row(row, token=token, browser_binding=browser_binding, now=current)
    row.consumed_at = current
    await db.flush()
    return row


def set_oauth_browser_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        OAUTH_BROWSER_COOKIE,
        value,
        max_age=int(INTENT_TTL.total_seconds()),
        path=OAUTH_BROWSER_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_oauth_browser_cookie(response: Response) -> None:
    response.set_cookie(
        OAUTH_BROWSER_COOKIE,
        "",
        max_age=0,
        expires=0,
        path=OAUTH_BROWSER_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
