"""Admin-only authentication helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, Query, status

from app.core.config import get_settings

settings = get_settings()


async def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    admin_token: str | None = Query(default=None, alias="admin_token"),
) -> None:
    """
    Guard for admin-only endpoints.

    Behavior:
    - If `ADMIN_TOKEN` is unset/empty: allow (dev mode).
    - If set: require header `X-Admin-Token: <ADMIN_TOKEN>`.
    """
    if not settings.admin_token:
        if settings.debug:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN not configured",
        )
    provided = x_admin_token or admin_token
    if not provided or provided != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
