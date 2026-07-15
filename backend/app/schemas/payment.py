"""Strict public and internal contracts for hosted payments."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints


Identifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Currency = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        to_upper=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
]


class NormalizedPaymentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: Identifier
    event_type: Identifier
    occurred_at: datetime
    object_id: Identifier
    request_id: Identifier | None = None
    customer_id: Identifier | None = None
    pre_tax_minor_units: int | None = Field(default=None, strict=True, ge=0)
    tax_minor_units: int | None = Field(default=None, strict=True, ge=0)
    currency: Currency | None = None
    normalized_status: Identifier
    business_metadata: dict[Identifier, str] = Field(default_factory=dict, max_length=32)
    raw_payload_sha256: Annotated[
        str,
        StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    ]


class CreditPackCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    product_code: Identifier = Field(
        validation_alias=AliasChoices("product_code", "package_id"),
    )
    return_url: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048),
    ] | None = None


class CheckoutRedirect(BaseModel):
    # Stored JSON necessarily represents UUIDs as strings; field-level string
    # constraints remain strict while this boundary parses that UUID encoding.
    model_config = ConfigDict(extra="forbid")

    purchase_id: uuid.UUID
    provider: Literal["creem"]
    status: Literal["READY", "CONFIRMED"]
    checkout_url: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2048)]


class CreditPackStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    purchase_id: uuid.UUID
    provider: Literal["creem"]
    product_code: Identifier
    state: Literal[
        "PENDING",
        "PAID",
        "PARTIAL_RECONCILIATION_REQUIRED",
        "DISPUTED",
        "REVERSED",
        "UNKNOWN",
    ]
    checkout_url: str | None = None
    captured_minor_units: int = Field(strict=True, ge=0)
    tax_minor_units: int = Field(strict=True, ge=0)
    refunded_minor_units: int = Field(strict=True, ge=0)
    disputed_minor_units: int = Field(strict=True, ge=0)
    currency: Currency
    credits_granted: int = Field(strict=True, ge=0)
    accounting_balance: int = Field(strict=True)
    spendable_balance: int = Field(strict=True, ge=0)


class AcceptedPaymentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: Identifier
    created: bool
    processing_state: Literal["RECEIVED", "UNHANDLED", "APPLIED", "RECONCILIATION_REQUIRED"]
