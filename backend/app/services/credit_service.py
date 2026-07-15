"""User/Credit Service - pricing rules and DB-backed balance operations."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.user_credit import UserCredit
from app.services.billing_catalog_service import BillingCatalogUnavailable
from app.services.welcome_grant_service import WELCOME_GRANT_AMOUNT

DEFAULT_CREDITS = WELCOME_GRANT_AMOUNT
COST_SINGLE_GENERATION = 2
COST_DIRECTOR_GENERATION = COST_SINGLE_GENERATION
COST_COUPLE_LOCAL_GENERATION = 3
COST_LIVE_PORTRAIT = 6
COST_LIVE_PORTRAIT_EXTRA_BLOCK = 4
COST_PER_GENERATION = COST_SINGLE_GENERATION


class CreditAuthorityRequired(RuntimeError):
    """Raised when a retired balance-only mutation path is invoked."""

    def __init__(self, operation: str):
        self.code = "credit_authority_required"
        self.operation = operation
        super().__init__(f"{self.code}:{operation}")


def _to_user_uuid(user_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


def get_generation_cost(
    template_category: str | None,
    *,
    image_count: int = 1,
    director_mode: bool = False,
) -> int:
    _ = template_category
    _ = director_mode
    is_couple = image_count >= 2
    if is_couple:
        return COST_COUPLE_LOCAL_GENERATION
    return COST_SINGLE_GENERATION


def get_live_portrait_cost(*, seconds: int = 5) -> int:
    normalized_seconds = max(1, int(seconds or 5))
    if normalized_seconds <= 5:
        return COST_LIVE_PORTRAIT
    extra_blocks = (normalized_seconds - 1) // 5
    return COST_LIVE_PORTRAIT + (extra_blocks * COST_LIVE_PORTRAIT_EXTRA_BLOCK)


async def _get_or_create_credit_row(db: AsyncSession, user_id: uuid.UUID | str) -> UserCredit:
    user_uuid = _to_user_uuid(user_id)
    result = await db.execute(select(UserCredit).where(UserCredit.user_id == user_uuid))
    row = result.scalar_one_or_none()
    if row is None:
        row = UserCredit(user_id=user_uuid, balance=0)
        db.add(row)
        await db.flush()
    return row


async def grant_welcome_bonus(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    metadata: dict | None = None,
) -> bool:
    _ = (db, user_id, metadata)
    raise CreditAuthorityRequired("welcome_grant")


async def get_balance_async(db: AsyncSession, user_id: uuid.UUID | str) -> int:
    row = await _get_or_create_credit_row(db, user_id)
    return int(row.balance or 0)


async def deduct_credits_async(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    amount: int = COST_PER_GENERATION,
    *,
    transaction_type: CreditTransactionType = CreditTransactionType.GENERATION_DEBIT,
    source: str | None = None,
    source_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> bool:
    _ = (
        db, user_id, amount, transaction_type, source, source_id, description, metadata,
    )
    raise CreditAuthorityRequired("reservation_capture")


async def add_credits_async(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    amount: int,
    *,
    transaction_type: CreditTransactionType = CreditTransactionType.ADMIN_GRANT,
    source: str | None = None,
    source_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> int:
    _ = (
        db, user_id, amount, transaction_type, source, source_id, description, metadata,
    )
    raise CreditAuthorityRequired("root_grant")


async def add_credits_with_transaction_async(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    amount: int,
    *,
    transaction_type: CreditTransactionType = CreditTransactionType.ADMIN_GRANT,
    source: str | None = None,
    source_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> tuple[int, CreditTransaction]:
    _ = (
        db, user_id, amount, transaction_type, source, source_id, description, metadata,
    )
    raise CreditAuthorityRequired("root_grant")


async def refund_generation_credits_once_async(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    amount: int,
    *,
    order_id: uuid.UUID | str,
    failure_code: str | None = None,
    provider: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> tuple[int, bool]:
    _ = (
        db, user_id, amount, order_id, failure_code, provider, description, metadata,
    )
    raise CreditAuthorityRequired("reservation_refund")


async def reset_balance_async(db: AsyncSession, user_id: uuid.UUID | str, amount: int = DEFAULT_CREDITS) -> int:
    _ = (db, user_id, amount)
    raise CreditAuthorityRequired("admin_adjustment")


async def list_balances_async(db: AsyncSession, *, limit: int = 200) -> list[dict]:
    limit = max(1, min(2000, int(limit)))
    result = await db.execute(
        select(UserCredit).order_by(UserCredit.balance.desc(), UserCredit.updated_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [{"user_id": str(r.user_id), "balance": int(r.balance or 0)} for r in rows]


async def list_credit_transactions_async(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    limit: int = 100,
) -> list[CreditTransaction]:
    user_uuid = _to_user_uuid(user_id)
    limit = max(1, min(500, int(limit)))
    result = await db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_uuid)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def get_packages() -> list:
    """Retired sync lookup: catalog access requires an AsyncSession."""

    raise BillingCatalogUnavailable("database_catalog_required")


def get_package_by_id(package_id: str) -> dict | None:
    """Retired sync lookup retained only to fail legacy callers closed."""

    _ = package_id
    raise BillingCatalogUnavailable("database_catalog_required")
