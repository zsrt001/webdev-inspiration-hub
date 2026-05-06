"""Authentication Pydantic schemas."""

from uuid import UUID
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Schema for WeChat login request."""

    code: str


class LoginResponse(BaseModel):
    """Schema for login response with JWT token."""

    access_token: str
    token_type: str = "bearer"
    openid: str
    user_id: UUID


class SupabaseSessionRequest(BaseModel):
    """Supabase OAuth access token exchange request."""

    access_token: str = Field(min_length=16, max_length=8192)
