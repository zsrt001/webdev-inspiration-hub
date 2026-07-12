"""Privacy-preserving, deployment-bound acceptance identity authorizations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acceptance_identity_binding import AcceptanceIdentityBinding


MAX_BINDING_TTL_SECONDS = 86400


def compute_subject_hmac(key: str, provider: str, subject: str) -> str:
    clean_key = key.strip()
    clean_provider = provider.strip().lower()
    clean_subject = subject.strip()
    if not clean_key or not clean_provider or not clean_subject:
        raise ValueError("HMAC key, provider, and subject are required")
    message = f"vowpic.acceptance-identity.v1\0{clean_provider}\0{clean_subject}".encode("utf-8")
    return hmac.new(clean_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def validate_binding_request(
    *,
    provider: str,
    subject: str,
    environment: str,
    deployment_id: str,
    expires_at: datetime,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    values = {
        "provider": provider,
        "subject": subject,
        "environment": environment,
        "deployment_id": deployment_id,
        "actor": actor,
        "reason": reason,
    }
    missing = [name for name, value in values.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"missing binding fields: {', '.join(missing)}")
    if environment not in {"preview", "production"}:
        raise ValueError("environment must be preview or production")
    if expires_at.tzinfo is None or current.tzinfo is None:
        raise ValueError("binding timestamps must be timezone-aware")
    ttl = (expires_at - current).total_seconds()
    if ttl <= 0 or ttl > MAX_BINDING_TTL_SECONDS:
        raise ValueError("binding expiry must be within 86400 seconds")


async def create_acceptance_binding(
    db: AsyncSession,
    *,
    provider: str,
    subject: str,
    environment: str,
    deployment_id: str,
    expires_at: datetime,
    actor: str,
    reason: str,
    hmac_key: str,
    now: datetime | None = None,
) -> AcceptanceIdentityBinding:
    current = now or datetime.now(timezone.utc)
    validate_binding_request(
        provider=provider,
        subject=subject,
        environment=environment,
        deployment_id=deployment_id,
        expires_at=expires_at,
        actor=actor,
        reason=reason,
        now=current,
    )
    binding = AcceptanceIdentityBinding(
        provider=provider.strip().lower(),
        subject_hmac=compute_subject_hmac(hmac_key, provider, subject),
        environment=environment,
        deployment_id=deployment_id.strip(),
        expires_at=expires_at,
        actor=actor.strip(),
        reason=reason.strip(),
        created_at=current,
    )
    db.add(binding)
    await db.flush()
    return binding


async def lock_acceptance_binding(
    db: AsyncSession,
    *,
    provider: str,
    subject_hmac: str,
    environment: str,
    deployment_id: str,
    now: datetime | None = None,
) -> AcceptanceIdentityBinding | None:
    current = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AcceptanceIdentityBinding)
        .where(
            AcceptanceIdentityBinding.provider == provider.strip().lower(),
            AcceptanceIdentityBinding.subject_hmac == subject_hmac,
            AcceptanceIdentityBinding.environment == environment,
            AcceptanceIdentityBinding.deployment_id == deployment_id,
            AcceptanceIdentityBinding.expires_at > current,
            AcceptanceIdentityBinding.consumed_at.is_(None),
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def has_unconsumed_acceptance_binding(
    db: AsyncSession,
    *,
    provider: str,
    subject_hmac: str,
    environment: str,
    deployment_id: str,
    now: datetime,
) -> bool:
    result = await db.execute(
        select(AcceptanceIdentityBinding.id).where(
            AcceptanceIdentityBinding.provider == provider.strip().lower(),
            AcceptanceIdentityBinding.subject_hmac == subject_hmac,
            AcceptanceIdentityBinding.environment == environment,
            AcceptanceIdentityBinding.deployment_id == deployment_id,
            AcceptanceIdentityBinding.expires_at > now,
            AcceptanceIdentityBinding.consumed_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def consume_binding_row(
    binding: AcceptanceIdentityBinding,
    local_user_id: UUID,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    if binding.consumed_at is not None or binding.consumed_user_id is not None:
        return False
    if binding.expires_at <= current:
        return False
    binding.consumed_user_id = local_user_id
    binding.consumed_at = current
    return True


async def consume_acceptance_binding(
    db: AsyncSession,
    *,
    provider: str,
    subject: str,
    environment: str,
    deployment_id: str,
    local_user_id: UUID,
    hmac_key: str,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    subject_hmac = compute_subject_hmac(hmac_key, provider, subject)
    binding = await lock_acceptance_binding(
        db,
        provider=provider,
        subject_hmac=subject_hmac,
        environment=environment,
        deployment_id=deployment_id,
        now=current,
    )
    if binding is None:
        return False
    consumed = await consume_binding_row(binding, local_user_id, now=current)
    if consumed:
        await db.flush()
    return consumed
