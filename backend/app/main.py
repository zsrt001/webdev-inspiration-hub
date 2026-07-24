"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.config import get_settings
from app.core.error_response import (
    error_response,
    http_exception_handler,
    install_sensitive_path_log_filter,
    redact_sensitive_path,
    redact_sentry_event,
    request_id_middleware,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.rate_limit import RateLimitMiddleware
from app.core.security_headers import (
    PROVIDER_PROBE_HEADER,
    is_authenticated_provider_probe,
    web_security_middleware,
)
from app.core.runtime_checks import (
    run_core_readiness_checks,
    run_readiness_checks,
    validate_commercial_config_values,
)
from app.services.runtime_bundle_service import public_runtime_bundle_json
from app.routers import api_router

settings = get_settings()
logger = logging.getLogger(__name__)
install_sensitive_path_log_filter()

# --- Sentry SDK (production only) ---
if not settings.debug and settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.1,
            environment=settings.runtime_environment,
            send_default_pii=False,
            before_send=redact_sentry_event,
        )
        logger.info("Sentry SDK initialized")
    except Exception as exc:
        logger.warning("Sentry init failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    strict_mode = not settings.debug

    config_errors = validate_commercial_config_values() if strict_mode else []
    app.state.runtime_config_blocked = bool(config_errors)
    if config_errors:
        logger.error(
            "Commercial runtime is not ready; serving the fail-closed liveness surface: %s",
            "; ".join(config_errors),
        )
    elif strict_mode:
        try:
            from app.core.database import async_session_maker
            from app.services.schema_guard_service import validate_runtime_schema

            async with async_session_maker() as db:
                await validate_runtime_schema(db)
        except Exception as exc:
            raise RuntimeError(f"database_schema_readiness_failed: {exc}") from exc

        core_readiness = await run_core_readiness_checks(strict_mode=True)
        if not core_readiness.get("ready", False):
            blockers = ", ".join(core_readiness.get("blockers", []))
            raise RuntimeError(f"core_runtime_readiness_failed: {blockers}")
        logger.info("Core runtime readiness checks passed")
    else:
        logger.info("Skipping blocking runtime readiness checks in dev mode")
    
    yield
    
    # Shutdown: close optional cache and database resources.
    try:
        from app.core.redis_client import close_redis
        await close_redis()
    except Exception as exc:
        logger.warning("Redis shutdown failed: %s", type(exc).__name__)

    try:
        from app.core.database import engine
        await engine.dispose()
    except Exception as exc:
        logger.warning("Database engine shutdown failed: %s", type(exc).__name__)


app = FastAPI(
    title=settings.app_name,
    description="VowPic Web SaaS API for identity, billing, generation, and private delivery",
    version="0.1.0",
    lifespan=lifespan,
)

_RUNTIME_CONFIG_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/ready",
        "/version",
        "/api/v1/ops/health",
        "/api/v1/ops/readiness",
    }
)
_CREEM_WEBHOOK_PATH = "/api/v1/payments/webhook/creem"
_EVOLINK_CALLBACK_PATH = re.compile(
    r"^/api/v1/provider-callbacks/evolink/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{64}$",
    re.IGNORECASE,
)


def _request_host(host_header: str) -> str:
    """Normalize the bounded DNS Host form used by Vercel aliases."""
    value = str(host_header or "").strip().lower()
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value


def _creem_callback_request_is_allowed(
    *,
    host_header: str,
    method: str,
    path: str,
) -> bool:
    """Expose only the signed Creem webhook on the dedicated callback alias."""
    callback_host = settings.creem_callback_host.strip().lower()
    if not callback_host or _request_host(host_header) != callback_host:
        return True
    return method.upper() == "POST" and path == _CREEM_WEBHOOK_PATH


def _evolink_callback_request_is_allowed(
    *,
    host_header: str,
    method: str,
    path: str,
    probe_secret: str = "",
) -> bool:
    """Expose only the signed callback and authenticated runtime probe."""

    explicit_origin = str(settings.evolink_callback_base_url or "").strip()
    try:
        callback_host = (urlsplit(explicit_origin).hostname or "").lower()
    except ValueError:
        callback_host = ""
    if not callback_host or _request_host(host_header) != callback_host:
        return True
    if method.upper() == "POST" and _EVOLINK_CALLBACK_PATH.fullmatch(path):
        return True
    return is_authenticated_provider_probe(
        method=method,
        path=path,
        probe_secret=probe_secret,
        settings_obj=settings,
    )


async def creem_callback_host_guard_middleware(request: Request, call_next):
    host = request.headers.get("host", "")
    method = request.method
    path = request.url.path
    if (
        not _creem_callback_request_is_allowed(
            host_header=host,
            method=method,
            path=path,
        )
        or not _evolink_callback_request_is_allowed(
            host_header=host,
            method=method,
            path=path,
            probe_secret=request.headers.get(PROVIDER_PROBE_HEADER, ""),
        )
    ):
        return error_response(
            request=request,
            status_code=404,
            detail={
                "code": "route_not_found",
                "message": "The requested route is not available on this host.",
            },
        )
    return await call_next(request)


async def runtime_config_guard_middleware(request: Request, call_next):
    """Keep misconfigured hosted deployments alive but unable to serve application APIs."""
    runtime_state = getattr(request.app.state, "runtime_config_blocked", None)
    if runtime_state is None:
        runtime_blocked = not settings.debug
    else:
        runtime_blocked = bool(runtime_state)
    if runtime_blocked and request.url.path not in _RUNTIME_CONFIG_EXEMPT_PATHS:
        logger.warning(
            "Blocked application request because the hosted runtime is not ready: path=%s",
            redact_sensitive_path(request.url.path),
        )
        return error_response(
            request=request,
            status_code=503,
            detail={
                "code": "runtime_not_ready",
                "message": "This deployment is not ready to serve application requests.",
                "action": "Use the liveness or readiness endpoint for diagnostics.",
            },
        )
    return await call_next(request)

app.middleware("http")(runtime_config_guard_middleware)
app.middleware("http")(creem_callback_host_guard_middleware)
app.add_middleware(RateLimitMiddleware)
app.middleware("http")(request_id_middleware)
app.middleware("http")(web_security_middleware)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Mount static files directory for backend-owned assets
import os
from fastapi.staticfiles import StaticFiles

static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount frontend style previews for template covers (style-aligned images)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
frontend_static_candidates = [
    os.path.join(project_root, "frontend", "src", "static"),
    os.path.join(project_root, "frontend", "static"),
]
frontend_static_dir = next((path for path in frontend_static_candidates if os.path.isdir(path)), "")
if frontend_static_dir:
    app.mount("/style-previews", StaticFiles(directory=frontend_static_dir), name="style-previews")
else:
    logger.warning(f"frontend_static_missing: {frontend_static_candidates}")

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Liveness probe: the process is running."""
    return {
        "status": "healthy",
        "kind": "liveness",
        "readiness": "/health/ready",
    }


@app.get("/version")
async def runtime_version():
    """Compatibility alias for the versioned public attestation route."""
    return public_runtime_bundle_json(settings)


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: core API dependencies are reachable."""
    report = await run_core_readiness_checks()
    return JSONResponse(
        status_code=200 if report.get("ready") else 503,
        content=report,
    )
