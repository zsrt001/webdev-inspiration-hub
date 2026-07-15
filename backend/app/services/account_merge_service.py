"""Controlled proof-bound merge for legacy accounts without commercial facts."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_claim_proof import AccountClaimProof
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus
from app.models.auth_session import AuthSession
from app.models.credit_purchase import CreditPurchase
from app.models.credit_transaction import CreditTransaction
from app.models.live_portrait_job import LivePortraitJob
from app.models.order import Order
from app.models.partner_consent_case import PartnerConsentCase
from app.models.partner_invite import PartnerInvite
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.user import User
from app.models.user_account_merge import UserAccountMerge
from app.models.user_credit import UserCredit
from app.models.user_identity import UserIdentity
from app.models.user_subscription import UserSubscription
from app.services.account_merge_credit_service import (
    AccountMergeCreditError,
    merge_credit_accounts,
)


class AccountClaimError(ValueError):
    """Stable business error for an account-claim refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_MERGEABLE_COMMERCIAL_FOOTPRINT_MODELS = (
    UserCredit,
    CreditTransaction,
    CreditPurchase,
    Order,
    UserSubscription,
    SubscriptionCreditGrant,
)


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def _is_active_identity(identity: UserIdentity) -> bool:
    return identity.revoked_at is None


async def _locked_rows(db: AsyncSession, model, *, user_id: uuid.UUID) -> list:
    rows = await db.scalars(
        select(model).where(model.user_id == user_id).limit(1).with_for_update()
    )
    return list(rows.all())


async def _revoke_legacy_sessions(
    db: AsyncSession,
    *,
    legacy_user_id: uuid.UUID,
    now: datetime,
) -> None:
    sessions = list(
        (
            await db.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == legacy_user_id)
                .with_for_update()
            )
        ).all()
    )
    tokens = list(
        (
            await db.scalars(
                select(AuthRefreshToken)
                .join(AuthSession, AuthSession.id == AuthRefreshToken.session_id)
                .where(AuthSession.user_id == legacy_user_id)
                .with_for_update()
            )
        ).all()
    )
    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = now
            session.token_version = int(session.token_version) + 1
    for token in tokens:
        if token.status != RefreshTokenStatus.REVOKED:
            token.status = RefreshTokenStatus.REVOKED
            token.revoked_at = now


def _anonymize_merged_profile(user: User) -> None:
    user.status = "merged"
    user.role = "user"
    for field in (
        "openid",
        "username",
        "password",
        "auth_provider",
        "auth_subject",
        "email",
        "email_verified_at",
        "unionid",
        "nickname",
        "avatar_url",
        "last_login_at",
    ):
        setattr(user, field, None)


async def claim_legacy_account(
    db: AsyncSession,
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    verified_proof_id: uuid.UUID | None,
    audit_request_id: str,
    now: datetime | None = None,
) -> UserAccountMerge:
    """Merge one empty legacy profile after exact one-time proof verification.

    Commercial balances and mutable ownership are transferred only through
    paired compensation facts; unsupported legacy/subscription branches fail.
    """

    if canonical_user_id == legacy_user_id:
        raise AccountClaimError("self_merge_forbidden")
    if verified_proof_id is None:
        raise AccountClaimError("ownership_proof_required")
    current_time = _now(now)

    users = list(
        (
            await db.scalars(
                select(User)
                .where(User.id.in_((canonical_user_id, legacy_user_id)))
                .order_by(User.id)
                .with_for_update()
            )
        ).all()
    )
    users_by_id = {user.id: user for user in users}
    canonical = users_by_id.get(canonical_user_id)
    legacy = users_by_id.get(legacy_user_id)
    if canonical is None or legacy is None:
        raise AccountClaimError("claim_target_not_found")
    if (canonical.status or "").strip().lower() != "active":
        raise AccountClaimError("canonical_account_inactive")

    proof = await db.scalar(
        select(AccountClaimProof)
        .where(AccountClaimProof.id == verified_proof_id)
        .with_for_update()
    )
    if proof is None:
        raise AccountClaimError("ownership_proof_required")
    if (
        proof.canonical_user_id != canonical_user_id
        or proof.legacy_user_id != legacy_user_id
    ):
        raise AccountClaimError("ownership_proof_mismatch")
    if proof.consumed_at is not None or proof.consumed_by_merge_id is not None:
        raise AccountClaimError("ownership_proof_consumed")
    if proof.expires_at <= current_time:
        raise AccountClaimError("ownership_proof_expired")

    graph = list(
        (
            await db.scalars(
                select(UserAccountMerge)
                .where(
                    or_(
                        UserAccountMerge.legacy_user_id.in_((canonical_user_id, legacy_user_id)),
                        UserAccountMerge.canonical_user_id == legacy_user_id,
                    )
                )
                .with_for_update()
            )
        ).all()
    )
    if graph:
        raise AccountClaimError("merge_graph_conflict")

    identities = list(
        (
            await db.scalars(
                select(UserIdentity)
                .where(UserIdentity.user_id.in_((canonical_user_id, legacy_user_id)))
                .with_for_update()
            )
        ).all()
    )
    canonical_identities = [
        item
        for item in identities
        if item.user_id == canonical_user_id
        and item.provider == "supabase"
        and _is_active_identity(item)
    ]
    if len(canonical_identities) != 1:
        raise AccountClaimError("canonical_identity_required")
    if any(
        item.user_id == legacy_user_id and _is_active_identity(item)
        for item in identities
    ):
        raise AccountClaimError("legacy_identity_not_claimable")

    if await _locked_rows(db, LivePortraitJob, user_id=legacy_user_id):
        raise AccountClaimError("legacy_feature_reconciliation_required")
    has_mergeable_commercial_footprint = False
    for model in _MERGEABLE_COMMERCIAL_FOOTPRINT_MODELS:
        if await _locked_rows(db, model, user_id=legacy_user_id):
            has_mergeable_commercial_footprint = True
    partner_invite_id = await db.scalar(
        select(PartnerInvite.id)
        .where(
            or_(
                PartnerInvite.host_user_id == legacy_user_id,
                PartnerInvite.partner_user_id == legacy_user_id,
            )
        )
        .limit(1)
        .with_for_update()
    )
    partner_case_id = await db.scalar(
        select(PartnerConsentCase.id)
        .where(
            or_(
                PartnerConsentCase.host_user_id == legacy_user_id,
                PartnerConsentCase.partner_user_id == legacy_user_id,
            )
        )
        .limit(1)
        .with_for_update()
    )
    has_mergeable_commercial_footprint = bool(
        has_mergeable_commercial_footprint
        or partner_invite_id is not None
        or partner_case_id is not None
    )
    if has_mergeable_commercial_footprint:
        try:
            await merge_credit_accounts(
                db,
                canonical_user_id=canonical_user_id,
                legacy_user_id=legacy_user_id,
                request_id=audit_request_id,
            )
        except AccountMergeCreditError as exc:
            raise AccountClaimError(exc.code) from exc

    await _revoke_legacy_sessions(db, legacy_user_id=legacy_user_id, now=current_time)
    merge = UserAccountMerge(
        id=uuid.uuid4(),
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        claim_proof_id=proof.id,
        audit_request_id=audit_request_id,
    )
    db.add(merge)
    await db.flush()

    # Task 6 consumes the proof inside the merge INSERT trigger under advisory
    # locks. Reload that authoritative value; never overwrite its timestamp.
    await db.refresh(proof)
    _anonymize_merged_profile(legacy)
    await db.flush()
    return merge
