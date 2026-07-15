"""Public subscription facts; no UI-derived billing state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Code = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    ),
]


class SubscriptionPlanRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Code
    pre_tax_minor_units: int = Field(strict=True, gt=0)
    currency: Literal["USD"]
    credits: int = Field(strict=True, gt=0)
    retention_tier: Code
    display_price: str


class CurrentSubscriptionRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subscription_id: uuid.UUID | None
    status: Literal[
        "NONE",
        "PENDING",
        "ACTIVE",
        "PAST_DUE",
        "CANCEL_REQUESTED",
        "CANCELED",
        "EXPIRED",
    ]
    product_code: Code | None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    paid_through_at: datetime | None = None
    cancel_at_period_end: bool
    credits_per_paid_period: int = Field(strict=True, ge=0)


class SubscriptionCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plan_code: Code
    return_url: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048),
    ] | None = None


class SubscriptionCheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["creem"]
    status: Literal["READY"]
    checkout_url: str


class SubscriptionCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: uuid.UUID
    state: Literal["CONFIRMED"]
    cancel_at_period_end: Literal[True]
