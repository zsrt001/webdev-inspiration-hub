"""Authentication Pydantic schemas."""

from uuid import UUID
from pydantic import BaseModel, Field


class LoginResponse(BaseModel):
    """Schema for login response with JWT token."""

    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    username: str | None = None


class SupabaseSessionRequest(BaseModel):
    """Supabase OAuth access token exchange request."""

    access_token: str = Field(min_length=16, max_length=8192)
