"""Strict public DTOs for authenticated Partner Invite operations."""

from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PartnerInviteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: uuid.UUID
    purpose: str
    status: str
    role: str
    version: int = Field(ge=1)
    expires_at: datetime
    order_intent_id: uuid.UUID
    order_intent_hash: str = Field(min_length=64, max_length=64)
    intent_policy_version: str
    template_id: str = Field(min_length=1, max_length=64)
    consent_event_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None


class PartnerInviteCreateRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    invite: PartnerInviteSnapshot
    token: str = Field(min_length=32, max_length=128)
    join_url: str = Field(min_length=1, max_length=2048)


class PartnerInviteAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=32, max_length=128)


class PartnerInviteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template_id: str = Field(min_length=1, max_length=64)


class PartnerInviteConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)
    order_intent_id: uuid.UUID
    order_intent_hash: str = Field(min_length=64, max_length=64)
    partner_asset_id: uuid.UUID


class PartnerInviteOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)
    host_asset_id: uuid.UUID
    consent_event_id: uuid.UUID


class PartnerInviteVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)


class PartnerWithdrawalRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    invite_id: uuid.UUID
    invite_status: str
    case_id: uuid.UUID | None = None
    case_status: str | None = None
    order_status: str | None = None
