"""User API routes."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.user_auth import get_request_user
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate

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
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_raise_legacy_user_route_retired)],
)
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create or get user from WeChat login."""
    _raise_legacy_user_route_retired()
    # Check if user exists
    result = await db.execute(
        select(User).where(User.openid == user_in.openid)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        return existing_user

    # Create new user
    user = User(**user_in.model_dump())
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.get(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(_raise_legacy_user_route_retired)],
)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get user by ID."""
    _raise_legacy_user_route_retired()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(_raise_legacy_user_route_retired)],
)
async def update_user(
    user_id: UUID,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Update user profile."""
    _raise_legacy_user_route_retired()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    update_data = user_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return user
