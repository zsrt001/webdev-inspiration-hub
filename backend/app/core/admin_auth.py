"""Separate browser Admin identity from backend-only service credentials."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.core.session_auth import get_session_user
from app.models.user import User


settings = get_settings()
ADMIN_ROLES = frozenset({"admin", "owner", "operator"})


def is_database_admin_user(user: User) -> bool:
    """Only the persisted database role can authorize a browser Admin session."""

    return (
        (user.status or "").strip().lower() == "active"
        and (user.role or "").strip().lower() in ADMIN_ROLES
    )


async def require_admin_user(
    request: Request,
    user: User = Depends(get_session_user),
) -> User:
    """Authorize the Admin UI with a revocable Cookie session and database role."""

    if not is_database_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "database_admin_required"},
        )
    request.state.admin_actor = f"admin-user:{user.id}"
    return user


async def require_service_admin_token(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """Backend-only credential dependency; never attach it to browser Admin routes."""

    configured = settings.admin_token.strip()
    supplied = str(x_admin_token or "").strip()
    if not configured or not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "service_admin_required"})
    request.state.admin_actor = "admin-service"
