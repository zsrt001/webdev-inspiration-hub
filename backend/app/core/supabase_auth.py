"""Supabase Auth token verification and profile normalization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from jose import JWTError, jwt

from app.core.config import get_settings


@dataclass(frozen=True)
class SupabaseUserClaims:
    subject: str
    email: str | None = None
    provider: str = "supabase"
    nickname: str | None = None
    avatar_url: str | None = None


class SupabaseAuthError(Exception):
    """Raised when a Supabase access token cannot be verified."""


def supabase_issuer_from_url(supabase_url: str) -> str:
    raw = str(supabase_url or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw if raw.startswith(("http://", "https://")) else f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/auth/v1"


def build_supabase_openid(subject: str) -> str:
    normalized = str(subject or "").strip()
    base = f"supabase:{normalized}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"supabase:{digest[:55]}"


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_supabase_claims(payload: dict) -> SupabaseUserClaims:
    subject = _first_text(payload.get("sub"), payload.get("id"))
    if not subject:
        raise SupabaseAuthError("supabase_token_missing_subject")

    app_metadata = payload.get("app_metadata")
    if not isinstance(app_metadata, dict):
        app_metadata = {}
    user_metadata = payload.get("user_metadata")
    if not isinstance(user_metadata, dict):
        user_metadata = {}

    provider = _first_text(
        app_metadata.get("provider"),
        payload.get("provider"),
        "supabase",
    ) or "supabase"
    nickname = _first_text(
        user_metadata.get("name"),
        user_metadata.get("full_name"),
        user_metadata.get("nickname"),
        payload.get("name"),
    )
    avatar_url = _first_text(
        user_metadata.get("avatar_url"),
        user_metadata.get("picture"),
        payload.get("avatar_url"),
        payload.get("picture"),
    )

    return SupabaseUserClaims(
        subject=subject,
        email=_first_text(payload.get("email")),
        provider=provider,
        nickname=nickname,
        avatar_url=avatar_url,
    )


async def verify_supabase_token(token: str) -> SupabaseUserClaims:
    settings = get_settings()
    access_token = str(token or "").strip()
    if not access_token:
        raise SupabaseAuthError("missing_supabase_token")

    if settings.supabase_jwt_secret:
        issuer = supabase_issuer_from_url(settings.supabase_url)
        decode_options = {"verify_aud": bool(settings.supabase_jwt_audience)}
        try:
            payload = jwt.decode(
                access_token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience or None,
                issuer=issuer or None,
                options=decode_options,
            )
        except JWTError as exc:
            raise SupabaseAuthError("invalid_supabase_jwt") from exc
        return parse_supabase_claims(payload)

    if settings.supabase_url and settings.supabase_anon_key:
        user_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {access_token}",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.supabase_auth_timeout, trust_env=False) as client:
                response = await client.get(user_url, headers=headers)
        except httpx.HTTPError as exc:
            raise SupabaseAuthError("supabase_userinfo_unreachable") from exc
        if response.status_code != 200:
            raise SupabaseAuthError(f"supabase_userinfo_rejected:{response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise SupabaseAuthError("supabase_userinfo_invalid")
        return parse_supabase_claims(data)

    raise SupabaseAuthError("supabase_auth_not_configured")
