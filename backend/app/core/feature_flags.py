"""Typed contracts for audited, server-authoritative capability gates."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from enum import StrEnum
from uuid import UUID

from fastapi import HTTPException

from app.core.config import get_settings


settings = get_settings()


class Capability(StrEnum):
    GOOGLE_AUTH = "google_auth"
    AUTHENTICATED_UPLOAD = "authenticated_upload"
    GENERATION = "generation"
    CREDIT_PACK_CHECKOUT = "credit_pack_checkout"
    SUBSCRIPTION_BILLING = "subscription_billing"
    PRIVATE_DOWNLOAD = "private_download"
    PARTNER_INVITE = "partner_invite"


_SETTING_BY_CAPABILITY = {
    Capability.GOOGLE_AUTH: "google_auth_enabled",
    Capability.AUTHENTICATED_UPLOAD: "authenticated_upload_enabled",
    Capability.GENERATION: "generation_enabled",
    Capability.CREDIT_PACK_CHECKOUT: "credit_pack_checkout_enabled",
    Capability.SUBSCRIPTION_BILLING: "subscription_billing_enabled",
    Capability.PRIVATE_DOWNLOAD: "private_download_enabled",
    Capability.PARTNER_INVITE: "partner_invite_enabled",
}


class FeatureFlagState(StrEnum):
    OFF = "OFF"
    ACCEPTANCE_COHORT = "ACCEPTANCE_COHORT"
    ON = "ON"


@dataclass(frozen=True)
class FeatureFlagContext:
    environment: str
    deployment_id: str | None = None
    runtime_bundle_id: str | None = None
    worker_image_digest: str | None = None
    user_id: UUID | None = None
    verified_identity_hash: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FeatureFlagDecision:
    capability: Capability
    state: FeatureFlagState
    allowed: bool
    snapshot_hash: str
    reason: str


def bootstrap_capability_enabled(capability: Capability) -> bool:
    """Retired compatibility surface that can never enable a capability."""
    _ = capability
    return False


def require_bootstrap_capability(capability: Capability) -> None:
    """Retired compatibility surface; active code uses PostgreSQL dependencies."""
    raise HTTPException(
        status_code=503,
        detail={"code": "capability_disabled", "capability": capability.value},
    )
