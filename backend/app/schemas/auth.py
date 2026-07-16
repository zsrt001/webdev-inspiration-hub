"""Cookie-session and PKCE exchange request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class OAuthIntentRequest(BaseModel):
    next_path: str = Field(default="/pages/account/index", min_length=1, max_length=512)


class OAuthIntentResponse(BaseModel):
    intent_token: str
    callback_url: str
    redirect_path: str
    expires_at: datetime


class SupabaseSessionRequest(BaseModel):
    """One-time broker token plus the browser's local application intent."""

    access_token: str = Field(min_length=16, max_length=8192)
    intent_token: str = Field(min_length=16, max_length=512)


class PaymentClaimProofRequest(BaseModel):
    legacy_user_id: UUID
    payment_reference: str = Field(min_length=1, max_length=256)


class PaymentClaimProofResponse(BaseModel):
    proof_id: UUID
    expires_at: datetime


class LegacyAccountMergeRequest(BaseModel):
    legacy_user_id: UUID
    proof_id: UUID


class LegacyAccountMergeResponse(BaseModel):
    merge_id: UUID
    legacy_user_id: UUID
    canonical_user_id: UUID
