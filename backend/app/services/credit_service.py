"""User/Credit Service - pricing rules and DB-backed balance operations."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.core.config import get_settings
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
COST_LIVE_PORTRAIT = 6
COST_LIVE_PORTRAIT_EXTRA_BLOCK = 4
COST_PER_GENERATION = COST_SINGLE_GENERATION
logger = logging.getLogger(__name__)
_credit_guardrails_ready = False
_credit_refund_guardrails_ready = False


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


async def ensure_generation_refund_guardrails(db: AsyncSession) -> None:
    """Best-effort DB guardrail for one refund transaction per generation order."""
    global _credit_refund_guardrails_ready
    if _credit_refund_guardrails_ready:
        return
    if not hasattr(db, "begin_nested"):
        _credit_refund_guardrails_ready = True
        return
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_transactions_order_refund_once
                    ON credit_transactions (user_id, source_id)
                    WHERE transaction_type = 'GENERATION_REFUND'
                      AND source = 'order'
                      AND source_id IS NOT NULL
                    """
                )
            )
        _credit_refund_guardrails_ready = True
    except Exception as exc:
        logger.warning("generation_refund_unique_index_unavailable: %s", exc)
        _credit_refund_guardrails_ready = True


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


async def _get_or_create_credit_row_for_update(db: AsyncSession, user_id: uuid.UUID | str) -> UserCredit:
    user_uuid = _to_user_uuid(user_id)
    if not isinstance(db, AsyncSession):
        return await _get_or_create_credit_row(db, user_uuid)

    await db.execute(
        pg_insert(UserCredit)
        .values(id=uuid.uuid4(), user_id=user_uuid, balance=0)
        .on_conflict_do_nothing(index_elements=[UserCredit.user_id])
    )
    result = await db.execute(
        select(UserCredit)
        .where(UserCredit.user_id == user_uuid)
        .with_for_update()
    )
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

    row = await _get_or_create_credit_row_for_update(db, user_uuid)
    existing = await db.execute(
        select(CreditTransaction).where(
            CreditTransaction.user_id == user_uuid,
            CreditTransaction.transaction_type == CreditTransactionType.WELCOME_BONUS,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False
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

    row = await _get_or_create_credit_row_for_update(db, user_id)
    if int(row.balance or 0) < amount_int:
        return False
    row.balance = int(row.balance or 0) - amount_int
    row.updated_at = func.now()
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
    row = await _get_or_create_credit_row_for_update(db, user_id)
    row.balance = int(row.balance or 0) + int(amount)
    row.updated_at = func.now()
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
    """Refund a failed generation once per order and return (balance, applied)."""
    amount_int = max(0, int(amount or 0))
    if amount_int <= 0:
        return await get_balance_async(db, user_id), False

    await ensure_generation_refund_guardrails(db)
    source_id = str(order_id)
    row = await _get_or_create_credit_row_for_update(db, user_id)
    existing = await db.execute(
        select(CreditTransaction)
        .where(
            CreditTransaction.user_id == _to_user_uuid(user_id),
            CreditTransaction.transaction_type == CreditTransactionType.GENERATION_REFUND,
            CreditTransaction.source == "order",
            CreditTransaction.source_id == source_id,
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return int(row.balance or 0), False

    row.balance = int(row.balance or 0) + amount_int
    row.updated_at = func.now()
    refund_metadata = dict(metadata or {})
    if failure_code:
        refund_metadata["failure_code"] = str(failure_code)
    if provider:
        refund_metadata["provider"] = str(provider)
    await _record_credit_transaction(
        db,
        user_id,
        transaction_type=CreditTransactionType.GENERATION_REFUND,
        amount=amount_int,
        balance_after=int(row.balance or 0),
        source="order",
        source_id=source_id,
        description=description or f"Generation failed: {failure_code or 'unknown_error'}",
        metadata=refund_metadata or None,
    )
    await db.flush()
    return int(row.balance or 0), True


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
    {"id": "pack_50", "credits": 50, "price": 12.90, "currency": "USD", "label": "AI Wedding Starter", "popular": False},
    {"id": "pack_120", "credits": 120, "price": 24.90, "currency": "USD", "label": "AI Wedding Popular", "popular": True},
    {"id": "pack_300", "credits": 300, "price": 49.90, "currency": "USD", "label": "AI Wedding Premium", "popular": False},
]


def get_packages() -> list:
    return get_credit_package_overrides() or CREDIT_PACKAGES


def _region_code(region: str | None, locale: str | None = None) -> str:
    raw = str(region or "").strip().upper()
    if raw:
        return raw[:8]
    normalized_locale = str(locale or "").strip().lower()
    if normalized_locale.startswith("zh"):
        return "CN"
    return "US"


def _payment_methods_for_region(region: str) -> list[str]:
    if region == "CN":
        return ["international_card", "manual_review"]
    if region in {"US", "CA", "AU", "GB", "EU"}:
        return ["card"]
    return ["card", "manual_review"]


def _format_price(amount: float, currency: str, locale: str | None = None) -> str:
    symbol = "$" if currency.upper() == "USD" else ""
    suffix = "" if currency.upper() == "USD" else f" {currency.upper()}"
    return f"{symbol}{amount:.2f}{suffix}"


def localize_credit_packages(packages: list[dict], *, region: str | None = None, locale: str | None = None) -> list[dict]:
    """Add region-ready pricing metadata without changing checkout settlement."""
    resolved_region = _region_code(region, locale)
    settings = get_settings()
    refund_url = settings.refund_policy_url or "/pages/legal/refund"
    localized: list[dict] = []
    for package in packages:
        item = dict(package)
        currency = str(item.get("currency") or "USD").upper()
        amount = float(item.get("price") or 0)
        item["currency"] = currency
        item["price_cents"] = int(round(amount * 100))
        item["display_price"] = _format_price(amount, currency, locale)
        item["region"] = resolved_region
        item["payment_methods"] = _payment_methods_for_region(resolved_region)
        item["refund_policy_url"] = refund_url
        item["localized_pricing_ready"] = False
        localized.append(item)
    return localized


def get_package_by_id(package_id: str) -> Optional[dict]:
    return next((p for p in get_packages() if p["id"] == package_id), None)
