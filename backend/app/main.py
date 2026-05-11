"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
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
            environment="production",
            send_default_pii=False,
        )
        logger.info("Sentry SDK initialized")
    except Exception as exc:
        logger.warning("Sentry init failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    strict_mode = not settings.debug

    if strict_mode:
        config_errors = validate_commercial_config_values()
        if config_errors:
            raise RuntimeError(f"commercial_config_invalid: {'; '.join(config_errors)}")

    try:
        from app.core.database import engine, Base
        async with engine.begin() as conn:
            if settings.should_auto_create_tables:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text("ALTER TABLE IF EXISTS leads ALTER COLUMN phone TYPE TEXT"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(32)"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS auth_subject VARCHAR(128)"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS email VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS username VARCHAR(64)"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS password VARCHAR(255)"))
                await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT 'user'"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'active'"))
                await conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITH TIME ZONE"))
        if settings.should_auto_create_tables:
            logger.info("Database connected and schema auto-create completed")
        else:
            logger.info("Database connected; schema auto-create disabled")
    except Exception as e:
        if strict_mode:
            raise RuntimeError(f"database_startup_failed: {e}") from e
        logger.warning(f"Database not available (dev mode): {e}")

    if strict_mode:
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

cors_origins = settings.cors_origins
if settings.debug:
    local_debug_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    if not cors_origins:
        cors_origins = local_debug_origins
    else:
        cors_origins = [*cors_origins, *(origin for origin in local_debug_origins if origin not in cors_origins)]

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

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
    }


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: core API dependencies are reachable."""
    report = await run_core_readiness_checks()
    return JSONResponse(
        status_code=200 if report.get("ready") else 503,
        content=report,
    )
