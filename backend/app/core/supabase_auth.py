"""Strict verification of short-lived Supabase Google broker sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import jwt
from jwt import PyJWTError

from app.core.config import get_settings
from app.core.google_identity import normalize_google_email


@dataclass(frozen=True)
class SupabaseUserClaims:
    """Broker-verified Google identity used only during the local-session exchange."""

    subject: str
    session_id: str
    issued_at: datetime
    email: str
    provider: str = "google"
    identity_provider: str = "supabase"
    broker_verified: bool = True
    nickname: str | None = None
    avatar_url: str | None = None


class SupabaseAuthError(Exception):
    """Raised when a Supabase broker session is absent, stale, or untrusted."""


def supabase_issuer_from_url(supabase_url: str) -> str:
    raw = str(supabase_url or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw if raw.startswith(("http://", "https://")) else f"https://{raw}")
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        return ""
    return f"https://{parsed.netloc}/auth/v1"


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _timestamp(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SupabaseAuthError(f"supabase_token_{field}_invalid")
    return value


def _uuid_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SupabaseAuthError(f"supabase_token_{field}_missing")
    try:
        return str(UUID(value.strip()))
    except (TypeError, ValueError) as exc:
        raise SupabaseAuthError(f"supabase_token_{field}_invalid") from exc


def _audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in value and all(isinstance(item, str) for item in value)
    return False


def _google_provider(metadata: dict[str, Any]) -> bool:
    provider = _first_text(metadata.get("provider"))
    providers = metadata.get("providers")
    return provider == "google" and isinstance(providers, list) and "google" in providers


def _verified_google_identity_email(user_record: dict[str, Any]) -> str:
    identities = user_record.get("identities")
    if not isinstance(identities, list):
        raise SupabaseAuthError("supabase_google_identity_missing")
    matches: list[str] = []
    for item in identities:
        if not isinstance(item, dict) or item.get("provider") != "google":
            continue
        identity_data = _dict(item.get("identity_data"))
        if identity_data.get("email_verified") is not True:
            continue
        try:
            matches.append(normalize_google_email(identity_data.get("email")))
        except ValueError:
            continue
    if len(matches) != 1:
        raise SupabaseAuthError("supabase_google_identity_ambiguous")
    return matches[0]


def _has_oauth_amr(payload: dict[str, Any]) -> bool:
    amr = payload.get("amr")
    if not isinstance(amr, list) or not amr:
        return False
    for entry in amr:
        if not isinstance(entry, dict) or entry.get("method") != "oauth":
            continue
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            return True
    return False


def parse_supabase_claims(
    payload: dict[str, Any],
    user_record: dict[str, Any],
    *,
    expected_issuer: str,
    expected_audience: str,
    now: datetime | None = None,
    maximum_age_seconds: int = 600,
    clock_skew_seconds: int = 60,
) -> SupabaseUserClaims:
    """Validate signed JWT facts against the broker's current user record."""

    if not isinstance(payload, dict) or not isinstance(user_record, dict):
        raise SupabaseAuthError("supabase_broker_payload_invalid")
    if not expected_issuer or payload.get("iss") != expected_issuer:
        raise SupabaseAuthError("supabase_token_issuer_invalid")
    if not expected_audience or not _audience_matches(payload.get("aud"), expected_audience):
        raise SupabaseAuthError("supabase_token_audience_invalid")
    if payload.get("role") != "authenticated":
        raise SupabaseAuthError("supabase_token_role_invalid")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    current_ts = int(current.timestamp())
    issued_at = _timestamp(payload.get("iat"), field="iat")
    expires_at = _timestamp(payload.get("exp"), field="exp")
    if issued_at > current_ts + max(0, clock_skew_seconds):
        raise SupabaseAuthError("supabase_token_iat_future")
    if current_ts - issued_at > max(1, maximum_age_seconds):
        raise SupabaseAuthError("supabase_token_iat_stale")
    if expires_at <= current_ts or expires_at <= issued_at:
        raise SupabaseAuthError("supabase_token_expired")

    subject = _uuid_text(payload.get("sub"), field="subject")
    session_id = _uuid_text(payload.get("session_id"), field="session_id")
    if payload.get("is_anonymous") is not False:
        raise SupabaseAuthError("supabase_anonymous_identity_forbidden")
    if not _has_oauth_amr(payload):
        raise SupabaseAuthError("supabase_google_amr_missing")

    signed_app_metadata = _dict(payload.get("app_metadata"))
    broker_app_metadata = _dict(user_record.get("app_metadata"))
    if not _google_provider(signed_app_metadata) or not _google_provider(broker_app_metadata):
        raise SupabaseAuthError("supabase_google_provider_required")
    google_identity_email = _verified_google_identity_email(user_record)

    broker_subject = _uuid_text(user_record.get("id"), field="broker_subject")
    if broker_subject != subject:
        raise SupabaseAuthError("supabase_broker_subject_mismatch")
    try:
        signed_email = normalize_google_email(payload.get("email"))
        broker_email = normalize_google_email(user_record.get("email"))
    except ValueError as exc:
        raise SupabaseAuthError("supabase_broker_email_invalid") from exc
    if signed_email != broker_email or broker_email != google_identity_email:
        raise SupabaseAuthError("supabase_broker_email_mismatch")
    if not _first_text(user_record.get("email_confirmed_at")):
        raise SupabaseAuthError("supabase_email_unverified")

    broker_user_metadata = _dict(user_record.get("user_metadata"))
    signed_user_metadata = _dict(payload.get("user_metadata"))
    nickname = _first_text(
        broker_user_metadata.get("name"),
        broker_user_metadata.get("full_name"),
        broker_user_metadata.get("nickname"),
        signed_user_metadata.get("name"),
    )
    avatar_url = _first_text(
        broker_user_metadata.get("avatar_url"),
        broker_user_metadata.get("picture"),
        signed_user_metadata.get("avatar_url"),
        signed_user_metadata.get("picture"),
    )
    return SupabaseUserClaims(
        subject=subject,
        session_id=session_id,
        issued_at=datetime.fromtimestamp(issued_at, tz=timezone.utc),
        email=broker_email,
        nickname=nickname,
        avatar_url=avatar_url,
    )


async def _fetch_supabase_user(access_token: str, *, url: str, publishable_key: str, timeout: float) -> dict[str, Any]:
    headers = {"apikey": publishable_key, "Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(f"{url.rstrip('/')}/auth/v1/user", headers=headers)
    except httpx.HTTPError as exc:
        raise SupabaseAuthError("supabase_userinfo_unreachable") from exc
    if response.status_code != 200:
        raise SupabaseAuthError("supabase_userinfo_rejected")
    try:
        data = response.json()
    except ValueError as exc:
        raise SupabaseAuthError("supabase_userinfo_invalid") from exc
    if not isinstance(data, dict):
        raise SupabaseAuthError("supabase_userinfo_invalid")
    return data


async def verify_supabase_token(token: str) -> SupabaseUserClaims:
    """Verify signature with Supabase Auth, then enforce the Google exchange contract."""

    settings = get_settings()
    access_token = str(token or "").strip()
    if not access_token or len(access_token) > 8192 or access_token.count(".") != 2:
        raise SupabaseAuthError("missing_or_malformed_supabase_token")
    issuer = supabase_issuer_from_url(settings.supabase_url)
    if not issuer or not settings.supabase_anon_key.strip():
        raise SupabaseAuthError("supabase_auth_not_configured")

    try:
        header = jwt.get_unverified_header(access_token)
        payload = jwt.decode(
            access_token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except PyJWTError as exc:
        raise SupabaseAuthError("invalid_supabase_jwt") from exc

    # When the project still uses HS256, verify locally as an additional check.
    # The broker /user call below remains the algorithm-independent signature authority.
    if header.get("alg") == "HS256" and settings.supabase_jwt_secret.strip():
        try:
            payload = jwt.decode(
                access_token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
                issuer=issuer,
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
            )
        except PyJWTError as exc:
            raise SupabaseAuthError("invalid_supabase_jwt") from exc

    user_record = await _fetch_supabase_user(
        access_token,
        url=settings.supabase_url,
        publishable_key=settings.supabase_anon_key,
        timeout=settings.supabase_auth_timeout,
    )
    return parse_supabase_claims(
        payload,
        user_record,
        expected_issuer=issuer,
        expected_audience=settings.supabase_jwt_audience,
        maximum_age_seconds=settings.supabase_exchange_max_token_age_seconds,
        clock_skew_seconds=settings.supabase_clock_skew_seconds,
    )
