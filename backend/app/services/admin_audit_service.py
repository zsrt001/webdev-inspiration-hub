"""Admin audit logging helpers."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import Request
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit_log import AdminAuditLog

logger = logging.getLogger(__name__)
_admin_audit_table_ready = False


async def ensure_admin_audit_table(db: AsyncSession) -> None:
    """Create the admin audit table on production runtimes that have not run migrations yet."""
    global _admin_audit_table_ready
    if _admin_audit_table_ready:
        return
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id UUID PRIMARY KEY,
                actor VARCHAR(128) NOT NULL,
                action VARCHAR(128) NOT NULL,
                request_method VARCHAR(16),
                request_path VARCHAR(512),
                ip_address VARCHAR(128),
                user_agent VARCHAR(512),
                details JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
    )
    for column in ("actor", "action", "request_path", "created_at"):
        await db.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_admin_audit_logs_{column}" ON admin_audit_logs ("{column}")'))
    _admin_audit_table_ready = True


def _actor_from_request(request: Request | None) -> str:
    if request is None:
        return "system"
    state_actor = getattr(request.state, "admin_actor", "")
    if state_actor:
        return str(state_actor)[:128]
    token = request.headers.get("x-admin-token") or ""
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return f"admin:{digest}"
    authorization = request.headers.get("authorization") or ""
    scheme, _, bearer = authorization.partition(" ")
    if scheme.lower() == "bearer" and bearer.strip():
        try:
            claims = jwt.get_unverified_claims(bearer.strip())
            subject = str(claims.get("sub") or "").strip()
            if subject:
                digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:12]
                return f"admin-user:{digest}"
        except JWTError:
            pass
    return "debug-admin"


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    return request.client.host if request.client else None


async def log_admin_action(
    db: AsyncSession,
    *,
    action: str,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> AdminAuditLog:
    """Persist a privileged-operation audit record without storing secrets."""
    log = AdminAuditLog(
        actor=_actor_from_request(request),
        action=action,
        request_method=request.method if request else None,
        request_path=str(request.url.path) if request else None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        details=details or {},
    )
    try:
        if hasattr(db, "begin_nested"):
            async with db.begin_nested():
                await ensure_admin_audit_table(db)
                db.add(log)
                await db.flush()
        else:
            db.add(log)
            await db.flush()
    except Exception as exc:
        logger.warning("admin_audit_log_failed: %s", exc)
    return log


async def list_admin_audit_logs(db: AsyncSession, *, limit: int = 50) -> list[AdminAuditLog]:
    await ensure_admin_audit_table(db)
    limit = max(1, min(200, int(limit)))
    result = await db.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit))
    return list(result.scalars().all())
