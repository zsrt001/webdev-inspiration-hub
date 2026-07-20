"""Commercial runtime readiness checks."""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker, control_plane_async_session_maker
from app.core.database_role_proof import validate_database_role_proof
from app.core.redis_client import get_redis
from app.core.task_queue import get_pool
from app.services.generation_service import generation_service
from app.services.job_lease_service import (
    GENERATION_SCHEMA_REVISION,
    WorkerHeartbeatInvalid,
    read_worker_runtime_heartbeat,
    worker_runtime_config_hash,
)
from app.services.runtime_bundle_service import public_runtime_bundle
from app.services.storage import DeleteResult, storage_service

settings = get_settings()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def _validate_public_base_url(name: str, value: str, *, allow_local_in_debug: bool = True) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return f"{name} is required"
    try:
        parsed = httpx.URL(raw)
    except Exception:
        return f"{name} must be a valid absolute http(s) URL"
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        return f"{name} must be a valid absolute http(s) URL"
    if not settings.debug and (parsed.host or "").lower() in {"localhost", "127.0.0.1"}:
        return f"{name} must be a public domain when DEBUG=false"
    if not allow_local_in_debug and (parsed.host or "").lower() in {"localhost", "127.0.0.1"}:
        return f"{name} must be public"
    return None


def _cors_origin_hosts() -> set[str]:
    hosts: set[str] = set()
    for item in settings.cors_origins:
        parsed = urlparse(item)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _redis_required() -> bool:
    return settings.using_background_queue


def _task_queue_required() -> bool:
    return settings.using_background_queue


def _worker_heartbeat_required() -> bool:
    return bool(settings.worker_image_digest.strip())


def validate_commercial_config_values() -> list[str]:
    errors: list[str] = []
    provider = settings.effective_storage_provider
    llm_provider = (settings.llm_provider or "").strip().lower()
    raw_payment_provider = (settings.payment_provider or "").strip().lower()

    if settings.runtime_environment == "development":
        errors.append("RUNTIME_ENVIRONMENT must be preview or production when DEBUG=false")
    else:
        errors.extend(settings.runtime_coordinate_errors)
        errors.extend(settings.control_plane_database_config_errors)

    if settings.generation_engine == "evolink":
        if not settings.evolink_api_key:
            errors.append("EVOLINK_API_KEY is required when GENERATION_ENGINE=evolink")
        if not settings.evolink_api_base_url:
            errors.append("EVOLINK_API_BASE_URL is required when GENERATION_ENGINE=evolink")
        if not settings.evolink_image_model:
            errors.append("EVOLINK_IMAGE_MODEL is required when GENERATION_ENGINE=evolink")
        if not settings.generation_image_model_allowed(settings.evolink_image_model):
            errors.append("EVOLINK_IMAGE_MODEL is not in the production allowlist")
    else:
        errors.append("GENERATION_ENGINE must be exactly evolink")
    if provider in {"", "local"}:
        errors.append("STORAGE_PROVIDER must be s3 or vercel (local is not commercial-safe)")
    if settings.allow_memory_fallback:
        errors.append("ALLOW_MEMORY_FALLBACK must be false")
    if settings.gatekeeper_allow_without_pillow:
        errors.append("GATEKEEPER_ALLOW_WITHOUT_PILLOW must be false")
    if settings.qa_allow_without_pillow:
        errors.append("QA_ALLOW_WITHOUT_PILLOW must be false")
    if not settings.qa_require_vision:
        errors.append("QA_REQUIRE_VISION must be true")
    if settings.secret_key == "change-me-in-production":
        errors.append("SECRET_KEY must be rotated")
    if len(str(settings.secret_key or "").encode("utf-8")) < 32:
        errors.append("SECRET_KEY must contain at least 32 bytes")
    if llm_provider in {"", "jiekou"}:
        if not settings.jiekou_api_key:
            errors.append("JIEKOU_API_KEY is required when LLM_PROVIDER=jiekou")
    elif llm_provider == "wenwen":
        if not settings.wenwen_vision_api_key_effective:
            errors.append("WENWEN_VISION_API_KEY is required when LLM_PROVIDER=wenwen")
        if not settings.wenwen_api_base_url:
            errors.append("WENWEN_API_BASE_URL is required when LLM_PROVIDER=wenwen")
    else:
        errors.append("LLM_PROVIDER must be jiekou or wenwen in commercial mode")
    if not settings.rate_limit_enabled:
        errors.append("RATE_LIMIT_ENABLED must be true")
    if not (settings.support_contact_email or settings.support_contact_url or settings.manual_payment_contact):
        errors.append("SUPPORT_CONTACT_EMAIL or SUPPORT_CONTACT_URL is required")
    if not settings.effective_cleanup_cron_token:
        errors.append("CLEANUP_CRON_TOKEN or CRON_SECRET is required for automatic image deletion")
    if not settings.cors_origins and not settings.is_vercel_runtime:
        errors.append("CORS_ALLOW_ORIGINS must not be empty")
    if raw_payment_provider not in {"creem", "manual", "manual_review", "manual-review", "offline"}:
        errors.append("PAYMENT_PROVIDER must be creem or manual_review")

    if provider == "s3":
        if not settings.aws_access_key_id or not settings.aws_secret_access_key:
            errors.append("S3 credentials are missing")
        if not settings.aws_s3_bucket:
            errors.append("AWS_S3_BUCKET is missing")
    if provider == "vercel":
        if not settings.blob_token_effective:
            errors.append("BLOB_READ_WRITE_TOKEN is missing")

    frontend_base_url_error = _validate_public_base_url(
        "FRONTEND_BASE_URL",
        settings.effective_frontend_base_url,
    )
    if frontend_base_url_error:
        errors.append(frontend_base_url_error)

    webhook_base_url_error = _validate_public_base_url(
        "WEBHOOK_BASE_URL",
        settings.effective_webhook_base_url,
    )
    if webhook_base_url_error:
        errors.append(webhook_base_url_error)

    if settings.provider_grant_origin:
        provider_grant_origin_error = _validate_public_base_url(
            "PROVIDER_GRANT_ORIGIN",
            settings.provider_grant_origin,
        )
        if provider_grant_origin_error:
            errors.append(provider_grant_origin_error)
        if len(settings.provider_grant_probe_secret) < 32:
            errors.append(
                "PROVIDER_GRANT_PROBE_SECRET must contain at least 32 characters when "
                "PROVIDER_GRANT_ORIGIN is configured"
            )

    if settings.cors_origins:
        frontend_host = urlparse((settings.effective_frontend_base_url or "").strip()).hostname
        if frontend_host and frontend_host.lower() not in _cors_origin_hosts():
            errors.append("CORS_ALLOW_ORIGINS must include FRONTEND_BASE_URL host")

    if settings.payment_mode == "creem":
        if not settings.creem_api_key:
            errors.append("CREEM_API_KEY is required when PAYMENT_PROVIDER=creem")
        if not settings.creem_webhook_secret:
            errors.append("CREEM_WEBHOOK_SECRET is required when PAYMENT_PROVIDER=creem")
        if not settings.creem_product_pack_50:
            errors.append("CREEM_PRODUCT_PACK_50 is required when PAYMENT_PROVIDER=creem")
        if not settings.creem_product_pack_120:
            errors.append("CREEM_PRODUCT_PACK_120 is required when PAYMENT_PROVIDER=creem")
        if not settings.creem_product_pack_300:
            errors.append("CREEM_PRODUCT_PACK_300 is required when PAYMENT_PROVIDER=creem")
        if settings.subscription_billing_enabled:
            if not settings.creem_subscription_starter_product_id:
                errors.append("CREEM_SUBSCRIPTION_STARTER_PRODUCT_ID is required when SUBSCRIPTION_BILLING_ENABLED=true")
            if not settings.creem_subscription_creator_product_id:
                errors.append("CREEM_SUBSCRIPTION_CREATOR_PRODUCT_ID is required when SUBSCRIPTION_BILLING_ENABLED=true")
            if not settings.creem_subscription_studio_product_id:
                errors.append("CREEM_SUBSCRIPTION_STUDIO_PRODUCT_ID is required when SUBSCRIPTION_BILLING_ENABLED=true")

    return errors


async def _check_database() -> tuple[bool, str]:
    async with async_session_maker() as db:
        await db.execute(text("SELECT 1"))
    return True, "ok"


async def _check_database_schema() -> tuple[bool, str]:
    expected = public_runtime_bundle(settings).schema_revision
    if not expected:
        raise RuntimeError("runtime schema revision is missing")
    async with async_session_maker() as db:
        actual = str(
            (await db.execute(text("SELECT version_num FROM alembic_version")))
            .scalar_one()
        )
    if actual != expected:
        return False, f"expected={expected},actual={actual}"
    return True, actual


async def _check_database_role(
    session_maker,
    required_group: str,
    forbidden_group: str,
) -> tuple[bool, str]:
    async with session_maker() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT current_user AS current_user,
                           role.rolcanlogin AS role_can_login,
                           role.rolinherit AS role_inherit,
                           role.rolsuper AS role_superuser,
                           role.rolcreatedb AS role_create_db,
                           role.rolcreaterole AS role_create_role,
                           role.rolreplication AS role_replication,
                           role.rolbypassrls AS role_bypass_rls,
                           pg_get_userbyid(control.relowner) AS control_table_owner,
                           pg_has_role(current_user, :required_group, 'MEMBER') AS required_group_member,
                           pg_has_role(current_user, :forbidden_group, 'MEMBER') AS forbidden_group_member
                    FROM pg_roles AS role
                    JOIN pg_class AS control
                      ON control.oid = 'public.ops_feature_flags'::regclass
                    WHERE role.rolname = current_user
                    """
                ),
                {
                    "required_group": required_group,
                    "forbidden_group": forbidden_group,
                },
            )
        ).mappings().one()
    detail = validate_database_role_proof(
        dict(row),
        required_group=required_group,
        forbidden_group=forbidden_group,
    )
    return True, detail


async def _check_redis() -> tuple[bool, str]:
    if not _redis_required():
        return True, "not_required"
    redis = await get_redis()
    pong = await redis.ping()
    if not pong:
        raise RuntimeError("redis ping failed")
    return True, "ok"


async def _check_task_queue() -> tuple[bool, str]:
    if not _task_queue_required():
        return True, "not_required"
    pool = await get_pool()
    pong = await pool.ping()
    if not pong:
        raise RuntimeError("task queue ping failed")
    return True, "ok"


async def _check_worker_heartbeat() -> tuple[bool, str]:
    if not _worker_heartbeat_required():
        return True, "not_required"
    try:
        redis = await get_redis()
        heartbeat = await read_worker_runtime_heartbeat(
            redis,
            environment=settings.runtime_environment,
            runtime_bundle_id=settings.runtime_bundle_id.strip().lower(),
        )
    except WorkerHeartbeatInvalid as exc:
        return False, str(exc)
    expected = {
        "source_sha": settings.source_sha,
        "api_deployment_id": settings.deployment_id,
        "worker_image_digest": settings.worker_image_digest.strip().lower(),
        "schema_revision": GENERATION_SCHEMA_REVISION,
        "payload_min": "generation-job.v1",
        "payload_max": "generation-job.v1",
        "config_hash": worker_runtime_config_hash(),
    }
    for field, value in expected.items():
        if getattr(heartbeat, field) != value:
            return False, f"worker_heartbeat_{field}_mismatch"
    if heartbeat.current_feature_snapshot_hash != heartbeat.target_feature_snapshot_hash:
        return False, "worker_feature_snapshot_mismatch"
    return True, "ok"


async def _check_generation_runtime() -> tuple[bool, str]:
    return await generation_service.ping_runtime()


async def _check_generation_queue() -> tuple[bool, str]:
    return await generation_service.probe_queue_capability()


def _check_storage_config() -> tuple[bool, str]:
    configured_storage_provider = (settings.storage_provider or "").strip().lower()
    provider = settings.effective_storage_provider
    if provider not in {"s3", "vercel", "local"}:
        raise RuntimeError(f"unsupported storage provider: {provider}")
    if provider == "local":
        return False, "local provider configured"
    if (
        configured_storage_provider == "s3"
        and settings.is_vercel_runtime
        and settings.aws_s3_endpoint_is_loopback
        and not settings.blob_token_effective
    ):
        raise RuntimeError(
            "AWS_S3_ENDPOINT points to local storage in Vercel; configure Vercel Blob or real S3/R2"
        )
    if provider == "s3":
        if not settings.aws_s3_bucket:
            raise RuntimeError("missing aws_s3_bucket")
        if not settings.aws_access_key_id or not settings.aws_secret_access_key:
            raise RuntimeError("missing aws credentials")
        return True, "ok"
    if not settings.blob_token_effective:
        raise RuntimeError("missing blob_read_write_token")
    return True, "ok"


def _check_payments_config() -> tuple[bool, str]:
    frontend_base_url_error = _validate_public_base_url(
        "FRONTEND_BASE_URL",
        settings.effective_frontend_base_url,
    )
    if frontend_base_url_error:
        raise RuntimeError(frontend_base_url_error)

    if settings.payment_mode == "manual_review":
        return True, "manual_review"

    missing: list[str] = []
    if not settings.creem_api_key:
        missing.append("CREEM_API_KEY")
    if not settings.creem_webhook_secret:
        missing.append("CREEM_WEBHOOK_SECRET")
    if not settings.creem_product_pack_50:
        missing.append("CREEM_PRODUCT_PACK_50")
    if not settings.creem_product_pack_120:
        missing.append("CREEM_PRODUCT_PACK_120")
    if not settings.creem_product_pack_300:
        missing.append("CREEM_PRODUCT_PACK_300")
    if settings.subscription_billing_enabled:
        if not settings.creem_subscription_starter_product_id:
            missing.append("CREEM_SUBSCRIPTION_STARTER_PRODUCT_ID")
        if not settings.creem_subscription_creator_product_id:
            missing.append("CREEM_SUBSCRIPTION_CREATOR_PRODUCT_ID")
        if not settings.creem_subscription_studio_product_id:
            missing.append("CREEM_SUBSCRIPTION_STUDIO_PRODUCT_ID")

    if missing:
        raise RuntimeError(f"missing payment config: {', '.join(missing)}")
    return True, "creem"


async def _probe_storage_rw() -> tuple[bool, str]:
    object_key = f"healthcheck/private/{secrets.token_hex(16)}.txt"
    payload = secrets.token_bytes(64)
    stored = False
    try:
        await asyncio.to_thread(
            storage_service.put_private,
            object_key,
            payload,
            "application/octet-stream",
        )
        stored = True
        content = await asyncio.to_thread(storage_service.read_private, object_key)
        if content != payload:
            raise RuntimeError("private storage read did not match the uploaded bytes")
    finally:
        if stored:
            deleted = await asyncio.to_thread(storage_service.delete_private, object_key)
            if deleted not in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
                raise RuntimeError("private storage probe cleanup failed")
    return True, "ok"


async def _run_check(name: str, coro, *, timeout_s: float = 5.0) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    try:
        ok, detail = await asyncio.wait_for(coro(), timeout=timeout_s)
        return name, {"ok": bool(ok), "detail": str(detail), "latency_ms": _ms(start)}
    except asyncio.TimeoutError:
        return name, {"ok": False, "detail": f"TimeoutError: exceeded {timeout_s:.1f}s", "latency_ms": _ms(start)}
    except Exception as e:
        return name, {"ok": False, "detail": f"{type(e).__name__}: {e}", "latency_ms": _ms(start)}


async def run_readiness_checks(
    *,
    probe_storage: bool = False,
    probe_generation_queue: bool = False,
    strict_mode: bool | None = None,
) -> dict[str, Any]:
    strict = (not settings.debug) if strict_mode is None else bool(strict_mode)
    checks: dict[str, dict[str, Any]] = {}

    config_errors = validate_commercial_config_values()
    checks["commercial_config"] = {
        "ok": len(config_errors) == 0,
        "detail": "ok" if not config_errors else "; ".join(config_errors),
        "latency_ms": 0.0,
    }
    if strict and config_errors:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strict_mode": True,
            "probe_storage": probe_storage,
            "probe_generation_queue": probe_generation_queue,
            "commercial_ready": False,
            "blockers": ["commercial_config"],
            "checks": checks,
        }

    name, result = await _run_check("database", _check_database, timeout_s=15.0)
    checks[name] = result
    if strict:
        name, result = await _run_check(
            "database_schema",
            _check_database_schema,
            timeout_s=15.0,
        )
        checks[name] = result
        name, result = await _run_check(
            "database_role",
            lambda: _check_database_role(
                async_session_maker,
                "vowpic_runtime",
                "vowpic_control_writer",
            ),
            timeout_s=15.0,
        )
        checks[name] = result
        name, result = await _run_check(
            "control_plane_database",
            lambda: _check_database_role(
                control_plane_async_session_maker,
                "vowpic_control_writer",
                "vowpic_runtime",
            ),
            timeout_s=15.0,
        )
        checks[name] = result
    name, result = await _run_check("redis", _check_redis)
    checks[name] = result
    name, result = await _run_check("task_queue", _check_task_queue)
    checks[name] = result
    name, result = await _run_check("worker_heartbeat", _check_worker_heartbeat)
    checks[name] = result
    name, result = await _run_check("generation_runtime", _check_generation_runtime)
    checks[name] = result
    if probe_generation_queue:
        name, result = await _run_check("generation_queue_probe", _check_generation_queue, timeout_s=90.0)
        checks[name] = result

    start = time.perf_counter()
    try:
        ok, detail = _check_storage_config()
        checks["storage_config"] = {"ok": bool(ok), "detail": str(detail), "latency_ms": _ms(start)}
    except Exception as e:
        checks["storage_config"] = {
            "ok": False,
            "detail": f"{type(e).__name__}: {e}",
            "latency_ms": _ms(start),
        }

    start = time.perf_counter()
    try:
        ok, detail = _check_payments_config()
        checks["payments_config"] = {"ok": bool(ok), "detail": str(detail), "latency_ms": _ms(start)}
    except Exception as e:
        checks["payments_config"] = {
            "ok": False,
            "detail": f"{type(e).__name__}: {e}",
            "latency_ms": _ms(start),
        }

    if probe_storage:
        name, result = await _run_check("storage_rw_probe", _probe_storage_rw)
        checks[name] = result

    required = ["database", "generation_runtime", "storage_config"]
    if _redis_required():
        required.append("redis")
    if _task_queue_required():
        required.append("task_queue")
    if _worker_heartbeat_required():
        required.append("worker_heartbeat")
    if strict:
        required.insert(0, "payments_config")
        required.insert(0, "commercial_config")
        required.extend(
            ["database_schema", "database_role", "control_plane_database"]
        )
    if probe_storage:
        required.append("storage_rw_probe")
    if probe_generation_queue:
        required.append("generation_queue_probe")

    blockers = [key for key in required if not checks.get(key, {}).get("ok", False)]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strict_mode": strict,
        "probe_storage": probe_storage,
        "probe_generation_queue": probe_generation_queue,
        "commercial_ready": len(blockers) == 0,
        "blockers": blockers,
        "checks": checks,
    }


async def run_core_readiness_checks(*, strict_mode: bool | None = None) -> dict[str, Any]:
    """Check only dependencies required for core API requests."""
    strict = (not settings.debug) if strict_mode is None else bool(strict_mode)
    checks: dict[str, dict[str, Any]] = {}

    if strict:
        config_errors = validate_commercial_config_values()
        checks["commercial_config"] = {
            "ok": len(config_errors) == 0,
            "detail": "ok" if not config_errors else "; ".join(config_errors),
            "latency_ms": 0.0,
        }
        if config_errors:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "not_ready",
                "ready": False,
                "strict_mode": True,
                "required": ["commercial_config"],
                "blockers": ["commercial_config"],
                "checks": checks,
            }

    name, result = await _run_check("database", _check_database, timeout_s=15.0)
    checks[name] = result
    if strict:
        name, result = await _run_check(
            "database_schema",
            _check_database_schema,
            timeout_s=15.0,
        )
        checks[name] = result
        name, result = await _run_check(
            "database_role",
            lambda: _check_database_role(
                async_session_maker,
                "vowpic_runtime",
                "vowpic_control_writer",
            ),
            timeout_s=15.0,
        )
        checks[name] = result
        name, result = await _run_check(
            "control_plane_database",
            lambda: _check_database_role(
                control_plane_async_session_maker,
                "vowpic_control_writer",
                "vowpic_runtime",
            ),
            timeout_s=15.0,
        )
        checks[name] = result
    name, result = await _run_check("redis", _check_redis)
    checks[name] = result
    name, result = await _run_check("task_queue", _check_task_queue)
    checks[name] = result
    name, result = await _run_check("worker_heartbeat", _check_worker_heartbeat)
    checks[name] = result

    required = ["database"]
    if strict:
        required.insert(0, "commercial_config")
        required.extend(
            ["database_schema", "database_role", "control_plane_database"]
        )
    if _redis_required():
        required.append("redis")
    if _task_queue_required():
        required.append("task_queue")
    if _worker_heartbeat_required():
        required.append("worker_heartbeat")

    blockers = [key for key in required if not checks.get(key, {}).get("ok", False)]
    ready = len(blockers) == 0
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "strict_mode": strict,
        "required": required,
        "blockers": blockers,
        "checks": checks,
    }
