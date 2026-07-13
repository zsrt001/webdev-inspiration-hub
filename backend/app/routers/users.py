"""User API routes."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException

from app.core.user_auth import get_request_user
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter()


def _raise_legacy_user_route_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={"code": "legacy_user_route_retired", "message": "Legacy OpenID user routes are retired."},
    )


@router.get("/me", response_model=UserRead)
async def get_current_user_profile(
    current_user: User = Depends(get_request_user),
) -> User:
    """Get current authenticated user's business profile."""
    return current_user


@router.post(
    "/",
    dependencies=[Depends(_raise_legacy_user_route_retired)],
    include_in_schema=False,
)
async def create_user() -> None:
    """Reject the retired caller-supplied identity route."""
    _raise_legacy_user_route_retired()


@router.get(
    "/{user_id}",
    dependencies=[Depends(_raise_legacy_user_route_retired)],
    include_in_schema=False,
)
async def get_user(user_id: UUID) -> None:
    """Reject the retired public user lookup route."""
    del user_id
    _raise_legacy_user_route_retired()


@router.patch(
    "/{user_id}",
    dependencies=[Depends(_raise_legacy_user_route_retired)],
    include_in_schema=False,
)
async def update_user(user_id: UUID) -> None:
    """Reject the retired public profile mutation route."""
    del user_id
    _raise_legacy_user_route_retired()
