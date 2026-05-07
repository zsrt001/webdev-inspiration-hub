"""Authentication Pydantic schemas."""

from uuid import UUID
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Schema for guest/code login or username/password login."""

    code: str | None = None
    username: str | None = Field(default=None, min_length=3, max_length=64)
    password: str | None = Field(default=None, min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    """Schema for password account registration."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class LoginResponse(BaseModel):
    """Schema for login response with JWT token."""

    access_token: str
    token_type: str = "bearer"
    openid: str
    user_id: UUID
    username: str | None = None


class SupabaseSessionRequest(BaseModel):
    """Supabase OAuth access token exchange request."""

    access_token: str = Field(min_length=16, max_length=8192)
