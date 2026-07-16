"""Creation of hash-only, server-verified legacy account claim proofs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import is_database_admin_user
from app.models.account_claim_proof import AccountClaimProof, AccountClaimProofType
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.payment_event import PaymentEvent
from app.models.user import User
from app.services.account_merge_service import AccountClaimError


CLAIM_PROOF_TTL = timedelta(minutes=15)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_CHANNEL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PAID_EVENT_MARKERS = ("paid", "succeeded", "completed")


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def _normalize_reference(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not normalized or len(normalized) > 256:
        raise AccountClaimError("ownership_proof_not_verified")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AccountClaimError("ownership_proof_not_verified")
    return normalized


def _reference_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _same_reference(left: object, right: str) -> bool:
    candidate = str(left or "").strip()
    return bool(candidate) and hmac.compare_digest(candidate, right)


def _status_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _support_channel_is_monitored(value: str) -> bool:
    channel = str(value or "").strip()
    return bool(_EMAIL_CHANNEL.fullmatch(channel) or channel.startswith("https://"))


async def _persist_proof(
    db: AsyncSession,
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    proof_type: AccountClaimProofType,
    reference_hash: str,
    verifier_actor: str,
    audit_request_id: str,
    now: datetime,
) -> AccountClaimProof:
    existing = await db.scalar(
        select(AccountClaimProof)
        .where(
            AccountClaimProof.proof_type == proof_type,
            AccountClaimProof.external_reference_hash == reference_hash,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.canonical_user_id == canonical_user_id
            and existing.legacy_user_id == legacy_user_id
            and existing.consumed_at is None
            and existing.expires_at > now
        ):
            return existing
        raise AccountClaimError("ownership_proof_unavailable")

    proof = AccountClaimProof(
        id=uuid.uuid4(),
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        proof_type=proof_type,
        external_reference_hash=reference_hash,
        verifier_actor=verifier_actor,
        verified_at=now,
        expires_at=now + CLAIM_PROOF_TTL,
        audit_request_id=audit_request_id,
    )
    db.add(proof)
    await db.flush()
    return proof


async def verify_payment_claim_reference(
    db: AsyncSession,
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    payment_reference: str,
    audit_request_id: str,
    now: datetime | None = None,
) -> AccountClaimProof:
    """Create proof only from an already processed, signed paid-provider fact."""

    reference = _normalize_reference(payment_reference)
    current_time = _now(now)
    purchases = list(
        (
            await db.scalars(
                select(CreditPurchase)
                .where(
                    CreditPurchase.user_id == legacy_user_id,
                    CreditPurchase.status == CreditPurchaseStatus.PAID,
                )
                .with_for_update()
            )
        ).all()
    )
    matched: CreditPurchase | None = None
    for purchase in purchases:
        if purchase.user_id != legacy_user_id:
            continue
        if _status_value(purchase.status) != CreditPurchaseStatus.PAID.value:
            continue
        references = (
            purchase.provider_request_id,
            purchase.provider_checkout_id,
            purchase.provider_payment_id,
            purchase.webhook_event_id,
        )
        if any(_same_reference(item, reference) for item in references):
            matched = purchase
            break
    if matched is None or not str(matched.webhook_event_id or "").strip():
        raise AccountClaimError("ownership_proof_not_verified")

    event = await db.scalar(
        select(PaymentEvent)
        .where(
            PaymentEvent.provider == matched.provider,
            PaymentEvent.event_id == matched.webhook_event_id,
        )
        .with_for_update()
    )
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    if (
        event is None
        or event.provider != matched.provider
        or event.event_id != matched.webhook_event_id
        or event.processed_at is None
        or event.error is not None
        or not any(marker in event_type for marker in _PAID_EVENT_MARKERS)
    ):
        raise AccountClaimError("ownership_proof_not_verified")

    return await _persist_proof(
        db,
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        proof_type=AccountClaimProofType.VERIFIED_PAYMENT,
        reference_hash=_reference_hash(reference),
        verifier_actor=f"payment-verifier:{matched.provider}",
        audit_request_id=audit_request_id,
        now=current_time,
    )


async def record_support_claim_proof(
    db: AsyncSession,
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    support_case_reference: str,
    audit_evidence_hash: str,
    admin_user: User,
    monitored_support_channel: str,
    audit_request_id: str,
    now: datetime | None = None,
) -> AccountClaimProof:
    """Record an internal support proof; no customer router exposes this action."""

    if not is_database_admin_user(admin_user):
        raise AccountClaimError("database_admin_required")
    if not _support_channel_is_monitored(monitored_support_channel):
        raise AccountClaimError("monitored_support_channel_required")
    evidence_hash = str(audit_evidence_hash or "").strip().lower()
    if not _HEX_SHA256.fullmatch(evidence_hash):
        raise AccountClaimError("support_audit_evidence_required")
    reference = _normalize_reference(support_case_reference)
    return await _persist_proof(
        db,
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        proof_type=AccountClaimProofType.VERIFIED_SUPPORT_CASE,
        reference_hash=_reference_hash(reference),
        verifier_actor=f"database-admin:{admin_user.id}",
        audit_request_id=audit_request_id,
        now=_now(now),
    )
