"""Public subscription API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SubscriptionPlanRead(BaseModel):
    code: str
    name: str
    billing_interval: str
    price_cents: int
    currency: str
    monthly_credits: int
    feature_flags: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class CurrentSubscriptionRead(BaseModel):
    status: str
    plan_code: str | None
    current_period_start: datetime | None = None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    monthly_credits: int = 0


class SubscriptionCheckoutRequest(BaseModel):
    plan_code: str
    return_url: str | None = None


class SubscriptionCheckoutResponse(BaseModel):
    provider: str
    status: str
    checkout_url: str


class SubscriptionCancelResponse(BaseModel):
    status: str
    cancel_at_period_end: bool
    current_period_end: datetime | None = None
