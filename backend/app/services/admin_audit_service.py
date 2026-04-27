"""Admin audit logging helpers."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit_log import AdminAuditLog


def _actor_from_request(request: Request | None) -> str:
    if request is None:
        return "system"
    token = request.headers.get("x-admin-token") or request.query_params.get("admin_token") or ""
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return f"admin:{digest}"
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
    db.add(log)
    await db.flush()
    return log


async def list_admin_audit_logs(db: AsyncSession, *, limit: int = 50) -> list[AdminAuditLog]:
    limit = max(1, min(200, int(limit)))
    result = await db.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit))
    return list(result.scalars().all())
