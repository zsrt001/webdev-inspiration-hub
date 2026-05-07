"""User Pydantic schemas."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class UserBase(BaseModel):
    """Base user schema."""

    nickname: str | None = None
    avatar_url: str | None = None


class UserCreate(BaseModel):
    """Schema for creating a user from WeChat login."""

    openid: str
    unionid: str | None = None
    nickname: str | None = None
    avatar_url: str | None = None
    email: str | None = None


class UserUpdate(UserBase):
    """Schema for updating user profile."""

    pass


class UserRead(UserBase):
    """Schema for reading user data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    openid: str
    username: str | None = None
    unionid: str | None = None
    auth_provider: str | None = None
    auth_subject: str | None = None
    email: str | None = None
    role: str = "user"
    status: str = "active"
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
