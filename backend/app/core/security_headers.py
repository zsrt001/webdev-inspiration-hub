"""Exact Web-origin validation and browser security-header policy."""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.release_activation import ReleaseActivation


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
    "connect-src 'self' https://*.supabase.co; img-src 'self' data: blob:; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    "upgrade-insecure-requests"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
CORS_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_HEADERS = (
    "Content-Type",
    "X-CSRF-Token",
    "X-Device-Id",
    "X-Request-ID",
    "Idempotency-Key",
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CORS_HEADER_NAMES = frozenset(value.lower() for value in CORS_HEADERS)
_PROVIDER_GRANT_READ_PATH = re.compile(r"^/api/v1/media/grants/[A-Za-z0-9_-]{20,128}$")
_PROVIDER_PROBE_PATH = "/api/v1/version"
PROVIDER_PROBE_HEADER = "x-vowpic-provider-probe"


def normalize_origin(value: str) -> str:
    """Return one canonical HTTP(S) origin or reject paths, credentials, and wildcards."""

    raw = str(value or "").strip()
    if not raw or raw == "null" or re.search(r"[\x00-\x20\x7f]", raw):
        raise ValueError("origin is missing or malformed")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("origin must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("origin credentials are forbidden")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("origin must not contain a path, query, or fragment")
    host = parsed.hostname.lower().rstrip(".")
    if not host or "*" in host:
        raise ValueError("origin host is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("origin port is invalid") from exc
    default_port = 443 if parsed.scheme == "https" else 80
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _settings_origin(value: str) -> str:
    try:
        return normalize_origin(value)
    except ValueError:
        return ""


def is_provider_grant_origin_request(
    *,
    host_header: str,
    request_scheme: str,
    settings_obj: Any | None = None,
) -> bool:
    """Identify the explicit isolated Provider host even when the request scheme is unsafe."""

    settings = settings_obj or get_settings()
    configured = _settings_origin(str(getattr(settings, "provider_grant_origin", "") or ""))
    raw_host = str(host_header or "").strip().lower()
    if not configured or not raw_host or any(token in raw_host for token in (",", " ", "\t", "@", "/")):
        return False
    if str(request_scheme or "").strip().lower() not in {"http", "https"}:
        return False
    try:
        candidate = normalize_origin(f"https://{raw_host}")
        expected = normalize_origin(configured)
    except ValueError:
        return False
    return candidate.removeprefix("https://") == expected.removeprefix("https://")


def is_exact_provider_grant_read(*, method: str, path: str) -> bool:
    return str(method or "").strip().upper() == "GET" and bool(
        _PROVIDER_GRANT_READ_PATH.fullmatch(str(path or ""))
    )


def is_authenticated_provider_probe(
    *,
    method: str,
    path: str,
    probe_secret: str,
    settings_obj: Any | None = None,
) -> bool:
    """Authorize only the exact read-only runtime probe using a separate app secret."""

    settings = settings_obj or get_settings()
    expected = str(getattr(settings, "provider_grant_probe_secret", "") or "")
    supplied = str(probe_secret or "")
    return (
        str(method or "").strip().upper() == "GET"
        and str(path or "") == _PROVIDER_PROBE_PATH
        and len(expected) >= 32
        and len(supplied) >= 32
        and hmac.compare_digest(supplied, expected)
    )


async def is_allowed_web_origin(
    origin: str,
    db: AsyncSession | None = None,
    *,
    settings_obj: Any | None = None,
    now: datetime | None = None,
) -> bool:
    """Authorize only the formal origin or an active PREVIEW_IDENTITY deployment."""

    settings = settings_obj or get_settings()
    try:
        candidate = normalize_origin(origin)
    except ValueError:
        return False

    formal = _settings_origin(settings.effective_frontend_base_url)
    if formal and candidate == formal:
        return True
    if settings.runtime_environment == "development":
        return candidate in {
            item for item in (_settings_origin(value) for value in settings.cors_origins) if item
        }

    if (
        settings.runtime_environment != "preview"
        or not settings.is_vercel_runtime
        or db is None
        or not settings.deployment_id
        or not re.fullmatch(r"rtb_[0-9a-f]{64}", settings.runtime_bundle_id.strip())
    ):
        return False
    staged = _settings_origin(f"https://{settings.vercel_url.strip()}")
    if not staged or candidate != staged:
        return False

    current = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(ReleaseActivation.id).where(
            ReleaseActivation.environment == "preview",
            ReleaseActivation.kind == "PREVIEW_IDENTITY",
            ReleaseActivation.phase == "COMPLETED",
            ReleaseActivation.api_role == "PREVIEW_IDENTITY",
            ReleaseActivation.api_deployment_id == settings.deployment_id,
            ReleaseActivation.runtime_bundle_id == settings.runtime_bundle_id.strip(),
            ReleaseActivation.api_deployment_url == staged,
            ReleaseActivation.reservation_expires_at.is_not(None),
            ReleaseActivation.reservation_expires_at > current,
        )
    )
    return result.scalar_one_or_none() is not None


async def require_request_origin(request: Request, db: AsyncSession) -> str:
    origin = (request.headers.get("origin") or "").strip()
    if not origin or not await is_allowed_web_origin(origin, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "origin_forbidden", "message": "Request origin is not allowed."},
        )
    return normalize_origin(origin)


def validate_preflight_request(requested_method: str, requested_headers: str) -> None:
    method = str(requested_method or "").strip().upper()
    if method not in CORS_METHODS:
        raise ValueError("CORS method is not allowed")
    names = {
        item.strip().lower()
        for item in str(requested_headers or "").split(",")
        if item.strip()
    }
    if not names.issubset(_CORS_HEADER_NAMES):
        raise ValueError("CORS header is not allowed")


def apply_web_security_headers(response: Response) -> None:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value


def _apply_cors_response_headers(response: Response, origin: str, *, preflight: bool) -> None:
    response.headers["Access-Control-Allow-Origin"] = normalize_origin(origin)
    response.headers["Access-Control-Allow-Credentials"] = "true"
    current_vary = [item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()]
    if not any(item.lower() == "origin" for item in current_vary):
        current_vary.append("Origin")
    response.headers["Vary"] = ", ".join(current_vary)
    if preflight:
        response.headers["Access-Control-Allow-Methods"] = ", ".join(CORS_METHODS)
        response.headers["Access-Control-Allow-Headers"] = ", ".join(CORS_HEADERS)
        response.headers["Access-Control-Max-Age"] = "600"


async def web_security_middleware(request: Request, call_next):
    """Apply exact-origin CORS and browser headers without wildcard credentials."""

    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip().lower()
    request_scheme = forwarded_proto or request.url.scheme
    runtime_settings = get_settings()
    provider_grant_origin_request = is_provider_grant_origin_request(
        host_header=str(request.headers.get("host") or ""),
        request_scheme=request_scheme,
        settings_obj=runtime_settings,
    )
    provider_route_allowed = is_exact_provider_grant_read(
        method=request.method,
        path=request.url.path,
    ) or is_authenticated_provider_probe(
        method=request.method,
        path=request.url.path,
        probe_secret=str(request.headers.get(PROVIDER_PROBE_HEADER) or ""),
        settings_obj=runtime_settings,
    )
    if provider_grant_origin_request and not provider_route_allowed:
        from app.core.error_response import error_response

        response = error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Resource not found."},
        )
        apply_web_security_headers(response)
        return response

    raw_origin = str(request.headers.get("origin") or "").strip()
    preflight = (
        request.method.upper() == "OPTIONS"
        and bool(request.headers.get("access-control-request-method"))
    )
    allowed_origin = False
    if raw_origin:
        allowed_origin = await is_allowed_web_origin(raw_origin)
        staged_candidate = ""
        if runtime_settings.runtime_environment == "preview" and runtime_settings.is_vercel_runtime:
            staged_candidate = _settings_origin(f"https://{runtime_settings.vercel_url.strip()}")
        if (
            not allowed_origin
            and staged_candidate
            and _settings_origin(raw_origin) == staged_candidate
        ):
            from app.core.database import async_session_maker

            async with async_session_maker() as db:
                allowed_origin = await is_allowed_web_origin(raw_origin, db)

    if preflight:
        try:
            if not allowed_origin:
                raise ValueError("CORS origin is not allowed")
            validate_preflight_request(
                request.headers.get("access-control-request-method") or "",
                request.headers.get("access-control-request-headers") or "",
            )
        except ValueError:
            from app.core.error_response import error_response

            response = error_response(
                request=request,
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "cors_forbidden", "message": "Cross-origin request is not allowed."},
            )
            apply_web_security_headers(response)
            return response
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        _apply_cors_response_headers(response, raw_origin, preflight=True)
        apply_web_security_headers(response)
        return response

    response = await call_next(request)
    if raw_origin and allowed_origin:
        _apply_cors_response_headers(response, raw_origin, preflight=False)
    apply_web_security_headers(response)
    return response
