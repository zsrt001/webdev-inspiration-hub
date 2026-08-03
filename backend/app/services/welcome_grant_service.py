"""Create one immutable welcome grant for one verified external identity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot, GrantLotSourceType
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.user_credit import UserCredit
from app.models.user_identity import UserIdentity
from app.models.welcome_grant_claim import WelcomeGrantClaim


WELCOME_GRANT_AMOUNT = 2
WELCOME_RETENTION_TIER = "welcome_30d"


class WelcomeGrantError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def ensure_welcome_grant_for_identity(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    now: datetime | None = None,
) -> WelcomeGrantClaim:
    """Lock identity and create claim, root ledger, lot, and balance atomically."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("welcome_grant_now_must_be_timezone_aware")
    identity = await db.scalar(
        select(UserIdentity)
        .where(UserIdentity.id == identity_id)
        .with_for_update()
    )
    if identity is None:
        raise WelcomeGrantError("verified_identity_required")
    if (
        identity.provider != "supabase"
        or identity.revoked_at is not None
        or not str(identity.verified_email_snapshot or "").strip()
    ):
        raise WelcomeGrantError("verified_identity_required")

    existing = await db.scalar(
        select(WelcomeGrantClaim).where(
            WelcomeGrantClaim.user_identity_id == identity.id
        )
    )
    if existing is not None:
        return existing
    existing_for_user = await db.scalar(
        select(WelcomeGrantClaim).where(WelcomeGrantClaim.user_id == identity.user_id)
    )
    if existing_for_user is not None:
        raise WelcomeGrantError("welcome_grant_already_owned")

    credit = await db.scalar(
        select(UserCredit)
        .where(UserCredit.user_id == identity.user_id)
        .with_for_update()
    )
    if credit is None:
        credit = UserCredit(
            id=uuid.uuid4(),
            user_id=identity.user_id,
            balance=0,
            reserved_balance=0,
        )
        db.add(credit)
        await db.flush()
    prior_balance = int(credit.balance or 0)
    next_balance = prior_balance + WELCOME_GRANT_AMOUNT
    debt_offset = min(WELCOME_GRANT_AMOUNT, max(0, -prior_balance))
    transaction_id = uuid.uuid4()
    lot_id = uuid.uuid4()
    transaction = CreditTransaction(
        id=transaction_id,
        user_id=identity.user_id,
        transaction_type=CreditTransactionType.WELCOME_BONUS,
        amount=WELCOME_GRANT_AMOUNT,
        balance_after=next_balance,
        source="verified_identity",
        source_id=str(identity.id),
        root_transaction_id=transaction_id,
        request_id=f"welcome:{identity.id}",
    )
    lot = CreditGrantLot(
        id=lot_id,
        user_id=identity.user_id,
        root_transaction_id=transaction_id,
        source_type=GrantLotSourceType.WELCOME,
        source_id=str(identity.id),
        original_amount=WELCOME_GRANT_AMOUNT,
        debt_offset_amount=debt_offset,
        reversed_amount=0,
        frozen_amount=0,
        consumed_amount=0,
        retention_tier=WELCOME_RETENTION_TIER,
        expires_at=current + timedelta(days=30),
    )
    claim = WelcomeGrantClaim(
        id=uuid.uuid4(),
        user_identity_id=identity.id,
        user_id=identity.user_id,
        credit_transaction_id=transaction_id,
        grant_lot_id=lot_id,
    )
    db.add(transaction)
    await db.flush()
    db.add(lot)
    await db.flush()
    db.add(claim)
    credit.balance = next_balance
    await db.flush()
    return claim
