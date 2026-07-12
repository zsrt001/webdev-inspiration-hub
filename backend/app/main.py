"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.config import get_settings
from app.core.error_response import (
    error_response,
    http_exception_handler,
    request_id_middleware,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.rate_limit import RateLimitMiddleware
from app.core.runtime_checks import (
    run_core_readiness_checks,
    run_readiness_checks,
    validate_commercial_config_values,
)
from app.routers import api_router

settings = get_settings()
logger = logging.getLogger(__name__)

# --- Sentry SDK (production only) ---
if not settings.debug and settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.1,
            environment=settings.runtime_environment,
            send_default_pii=False,
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
    
    # Shutdown: close queue/redis/db resources
    try:
        from app.core.task_queue import close_pool
        await close_pool()
    except Exception:
        pass

    try:
        from app.core.redis_client import close_redis
        await close_redis()
    except Exception:
        pass

    try:
        from app.core.database import engine
        await engine.dispose()
    except Exception:
        pass


app = FastAPI(
    title=settings.app_name,
    description="API for AI Wedding Photo generation using InstantID",
    version="0.1.0",
    lifespan=lifespan,
)

_RUNTIME_CONFIG_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/ready",
        "/api/v1/ops/health",
        "/api/v1/ops/readiness",
    }
)


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
            request.url.path,
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

cors_origins = settings.cors_origins
if settings.debug:
    local_debug_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    if not cors_origins:
        cors_origins = local_debug_origins
    else:
        cors_origins = [*cors_origins, *(origin for origin in local_debug_origins if origin not in cors_origins)]

# CORS middleware
app.middleware("http")(runtime_config_guard_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.middleware("http")(request_id_middleware)
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
        "app": settings.app_name,
        "readiness": "/health/ready",
        "source_sha": settings.source_sha,
        "runtime_bundle_id": settings.runtime_bundle_id.strip(),
        "deployment_id": settings.deployment_id,
    }


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: core API dependencies are reachable."""
    report = await run_core_readiness_checks()
    return JSONResponse(
        status_code=200 if report.get("ready") else 503,
        content=report,
    )
