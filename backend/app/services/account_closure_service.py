"""Soft account closure that preserves financial and media references."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_tombstone import AccountTombstone
from app.models.auth_refresh_token import AuthRefreshToken, RefreshTokenStatus
from app.models.auth_session import AuthSession
from app.models.media_asset import MediaAsset, MediaAssetStatus
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.services.media_deletion_service import request_asset_deletion


logger = logging.getLogger(__name__)


class AccountClosureError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_CLOSURE_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def _anonymize_profile(user: User) -> None:
    user.status = "closed"
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


async def close_account(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audit_request_id: str,
    closure_reason: str = "USER_REQUESTED",
    now: datetime | None = None,
) -> AccountTombstone:
    """Revoke sign-in immediately and retain a minimal auditable tombstone."""

    reason = str(closure_reason or "").strip().upper()
    if not _CLOSURE_REASON.fullmatch(reason):
        raise AccountClosureError("invalid_closure_reason")
    current_time = _now(now)
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AccountClosureError("account_not_found")

    identities = list(
        (
            await db.scalars(
                select(UserIdentity)
                .where(UserIdentity.user_id == user_id)
                .with_for_update()
            )
        ).all()
    )
    sessions = list(
        (
            await db.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == user_id)
                .with_for_update()
            )
        ).all()
    )
    refresh_tokens = list(
        (
            await db.scalars(
                select(AuthRefreshToken)
                .join(AuthSession, AuthSession.id == AuthRefreshToken.session_id)
                .where(AuthSession.user_id == user_id)
                .with_for_update()
            )
        ).all()
    )
    tombstone = await db.scalar(
        select(AccountTombstone)
        .where(AccountTombstone.user_id == user_id)
        .with_for_update()
    )

    for identity in identities:
        if identity.revoked_at is None:
            identity.revoked_at = current_time
    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = current_time
            session.token_version = int(session.token_version) + 1
    for token in refresh_tokens:
        if token.status != RefreshTokenStatus.REVOKED:
            token.status = RefreshTokenStatus.REVOKED
            token.revoked_at = current_time
    _anonymize_profile(user)

    if tombstone is None:
        tombstone = AccountTombstone(
            user_id=user_id,
            closure_reason=reason,
            closed_at=current_time,
            media_cleanup_pending=True,
            audit_request_id=audit_request_id,
        )
        db.add(tombstone)
    await db.flush()
    # Persist authentication revocation before attempting media cleanup.  A
    # storage/reference failure must never reopen a closed account.
    await db.commit()

    try:
        media_assets = list(
            (
                await db.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.owner_user_id == user_id)
                    .with_for_update()
                )
            ).all()
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Account closed but private media cleanup discovery failed for user %s",
            user_id,
        )
        return tombstone

    for asset in media_assets:
        if MediaAssetStatus(asset.status) == MediaAssetStatus.DELETED:
            continue
        try:
            await request_asset_deletion(
                db,
                asset.id,
                reason="account_closure",
                now=current_time,
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Account closed but private media deletion request failed for asset %s",
                asset.id,
            )
    return tombstone
