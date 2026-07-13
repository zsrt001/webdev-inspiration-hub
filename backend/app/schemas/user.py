"""User Pydantic schemas."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class UserBase(BaseModel):
    """Base user schema."""

    nickname: str | None = None
    avatar_url: str | None = None


class UserRead(UserBase):
    """Schema for reading user data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str | None = None
    email: str | None = None
    role: str = "user"
    status: str = "active"
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
