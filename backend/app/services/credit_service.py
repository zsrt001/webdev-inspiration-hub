"""User/Credit Service - pricing rules and DB-backed balance operations."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.user_credit import UserCredit
from app.services.ops_config_service import get_credit_package_overrides
from app.services.trial_access_service import _trial_welcome_credits

# Data file fallback for legacy/dev paths only.
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CREDITS_FILE = os.path.join(DATA_DIR, "credits.json")
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_CREDITS = _trial_welcome_credits()
COST_SINGLE_GENERATION = 2
COST_DIRECTOR_GENERATION = 3
COST_COUPLE_LOCAL_GENERATION = 3
COST_COUPLE_REMOTE_GENERATION = 4
COST_VINTAGE_GENERATION = 5
COST_LIVE_PORTRAIT = 6
COST_LIVE_PORTRAIT_EXTRA_BLOCK = 4
COST_PER_GENERATION = COST_SINGLE_GENERATION
logger = logging.getLogger(__name__)
_credit_guardrails_ready = False


async def ensure_credit_guardrails(db: AsyncSession) -> None:
    """Best-effort DB guardrail for the one-time welcome bonus."""
    global _credit_guardrails_ready
    if _credit_guardrails_ready:
        return
    if not hasattr(db, "begin_nested"):
        _credit_guardrails_ready = True
        return
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_transactions_welcome_once
                    ON credit_transactions (user_id)
                    WHERE transaction_type = 'WELCOME_BONUS'
                    """
                )
            )
        _credit_guardrails_ready = True
    except Exception as exc:
        logger.warning("welcome_bonus_unique_index_unavailable: %s", exc)
        _credit_guardrails_ready = True


def _to_user_uuid(user_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


def get_generation_cost(
    template_category: str | None,
    *,
    is_remote_join: bool = False,
    image_count: int = 1,
    director_mode: bool = False,
) -> int:
    if template_category == "vintage":
        return COST_VINTAGE_GENERATION
    is_couple = image_count >= 2
    if is_couple and is_remote_join:
        return COST_COUPLE_REMOTE_GENERATION
    if is_couple:
        return COST_COUPLE_LOCAL_GENERATION
    if director_mode:
        return COST_DIRECTOR_GENERATION
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
    """Grant welcome bonus credits. Returns True if granted, False if already claimed."""
    user_uuid = _to_user_uuid(user_id)
    await ensure_credit_guardrails(db)

    # Check if user already received welcome bonus
    existing = await db.execute(
        select(CreditTransaction).where(
            CreditTransaction.user_id == user_uuid,
            CreditTransaction.transaction_type == CreditTransactionType.WELCOME_BONUS,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False

    row = await _get_or_create_credit_row(db, user_uuid)
    row.balance += DEFAULT_CREDITS
    await db.flush()
    await _record_credit_transaction(
        db,
        user_uuid,
        transaction_type=CreditTransactionType.WELCOME_BONUS,
        amount=DEFAULT_CREDITS,
        balance_after=row.balance,
        source="system",
        description="Starter credits for verified account",
        metadata=metadata,
    )
    return True


async def _record_credit_transaction(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    transaction_type: CreditTransactionType,
    amount: int,
    balance_after: int,
    source: str | None = None,
    source_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> CreditTransaction:
    transaction = CreditTransaction(
        user_id=_to_user_uuid(user_id),
        transaction_type=transaction_type,
        amount=int(amount),
        balance_after=int(balance_after),
        source=source,
        source_id=source_id,
        description=description,
        metadata_json=metadata,
    )
    db.add(transaction)
    await db.flush()
    return transaction


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
    amount_int = int(amount)
    if amount_int <= 0:
        raise ValueError("Credit deduction amount must be positive")

    row = await _get_or_create_credit_row(db, user_id)
    if int(row.balance or 0) < amount_int:
        return False
    row.balance = int(row.balance or 0) - amount_int
    await _record_credit_transaction(
        db,
        user_id,
        transaction_type=transaction_type,
        amount=-amount_int,
        balance_after=int(row.balance or 0),
        source=source,
        source_id=source_id,
        description=description,
        metadata=metadata,
    )
    await db.flush()
    return True


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
    balance, _transaction = await add_credits_with_transaction_async(
        db,
        user_id,
        amount,
        transaction_type=transaction_type,
        source=source,
        source_id=source_id,
        description=description,
        metadata=metadata,
    )
    return balance


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
    row = await _get_or_create_credit_row(db, user_id)
    row.balance = int(row.balance or 0) + int(amount)
    transaction = await _record_credit_transaction(
        db,
        user_id,
        transaction_type=transaction_type,
        amount=int(amount),
        balance_after=int(row.balance or 0),
        source=source,
        source_id=source_id,
        description=description,
        metadata=metadata,
    )
    await db.flush()
    return int(row.balance or 0), transaction


async def reset_balance_async(db: AsyncSession, user_id: uuid.UUID | str, amount: int = DEFAULT_CREDITS) -> int:
    row = await _get_or_create_credit_row(db, user_id)
    delta = int(amount) - int(row.balance or 0)
    row.balance = int(amount)
    await _record_credit_transaction(
        db,
        user_id,
        transaction_type=CreditTransactionType.ADJUSTMENT,
        amount=delta,
        balance_after=int(amount),
        source="admin",
        description="Credit balance reset",
    )
    await db.flush()
    return int(row.balance or 0)


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


# Legacy fallback helpers (kept for compatibility with old scripts).
def _read_credits() -> dict:
    if not os.path.exists(CREDITS_FILE):
        return {}
    try:
        with open(CREDITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _write_credits(data: dict) -> None:
    with open(CREDITS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_balance(user_id: str = "anonymous_user") -> int:
    credits_data = _read_credits()
    if user_id not in credits_data:
        credits_data[user_id] = DEFAULT_CREDITS
        _write_credits(credits_data)
    return int(credits_data.get(user_id, DEFAULT_CREDITS) or 0)


def deduct_credits(amount: int = COST_PER_GENERATION, user_id: str = "anonymous_user") -> bool:
    credits_data = _read_credits()
    current = int(credits_data.get(user_id, DEFAULT_CREDITS) or 0)
    if current < int(amount):
        return False
    credits_data[user_id] = current - int(amount)
    _write_credits(credits_data)
    return True


def add_credits(amount: int, user_id: str = "anonymous_user") -> int:
    credits_data = _read_credits()
    current = int(credits_data.get(user_id, DEFAULT_CREDITS) or 0)
    new_balance = current + int(amount)
    credits_data[user_id] = new_balance
    _write_credits(credits_data)
    return new_balance


def can_generate(user_id: str = "anonymous_user") -> bool:
    return get_balance(user_id) >= COST_PER_GENERATION


def reset_balance(user_id: str = "anonymous_user", amount: int = DEFAULT_CREDITS) -> int:
    credits_data = _read_credits()
    credits_data[user_id] = int(amount)
    _write_credits(credits_data)
    return int(amount)


CREDIT_PACKAGES = [
    {"id": "pack_50", "credits": 50, "price": 12.90, "label": "AI Wedding Starter", "popular": False},
    {"id": "pack_120", "credits": 120, "price": 24.90, "label": "AI Wedding Popular", "popular": True},
    {"id": "pack_300", "credits": 300, "price": 49.90, "label": "AI Wedding Premium", "popular": False},
]


def get_packages() -> list:
    return get_credit_package_overrides() or CREDIT_PACKAGES


def get_package_by_id(package_id: str) -> Optional[dict]:
    return next((p for p in get_packages() if p["id"] == package_id), None)
