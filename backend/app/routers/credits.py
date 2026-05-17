"""Credits API routes for balance and payments."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.database import get_db
from app.core.user_auth import get_request_user
from app.models.user import User
from app.services.credit_service import (
    get_balance_async,
    add_credits_async,
    deduct_credits_async,
    get_packages,
    localize_credit_packages,
    get_package_by_id,
    list_credit_transactions_async,
    COST_PER_GENERATION,
)
from app.models.credit_transaction import CreditTransactionType

router = APIRouter()


class BalanceResponse(BaseModel):
    """User balance response."""
    balance: int
    can_generate: bool
    cost_per_generation: int


class CreditPackage(BaseModel):
    """Credit package model."""
    id: str
    credits: int
    price: float
    currency: str = "USD"
    price_cents: int = 0
    display_price: str | None = None
    region: str = "US"
    payment_methods: list[str] = Field(default_factory=list)
    refund_policy_url: str = "/pages/legal/refund"
    localized_pricing_ready: bool = False
    label: str
    popular: bool


class PackagesResponse(BaseModel):
    """Available packages response."""
    packages: List[CreditPackage]


class PurchaseRequest(BaseModel):
    """Purchase request."""
    package_id: str


class PurchaseResponse(BaseModel):
    """Purchase result response."""
    success: bool
    credits_added: int
    new_balance: int
    message: str


class AdminAddCreditsRequest(BaseModel):
    user_id: str
    amount: int


class CreditTransactionRead(BaseModel):
    id: str
    transaction_type: str
    amount: int
    balance_after: int
    source: str | None = None
    source_id: str | None = None
    description: str | None = None
    created_at: str | None = None


class CreditTransactionsResponse(BaseModel):
    transactions: list[CreditTransactionRead]


@router.get("/balance", response_model=BalanceResponse)
async def get_user_balance(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's credit balance."""
    balance = await get_balance_async(db, current_user.id)
    return BalanceResponse(
        balance=balance,
        can_generate=balance >= COST_PER_GENERATION,
        cost_per_generation=COST_PER_GENERATION,
    )


@router.get("/packages", response_model=PackagesResponse)
async def list_packages(
    region: str | None = Query(default=None, max_length=8),
    locale: str | None = Query(default=None, max_length=16),
):
    """Get available credit packages for purchase."""
    packages = localize_credit_packages(get_packages(), region=region, locale=locale)
    return PackagesResponse(packages=[CreditPackage(**p) for p in packages])


@router.get("/transactions", response_model=CreditTransactionsResponse)
async def list_credit_transactions(
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    """Get current user's credit ledger entries."""
    rows = await list_credit_transactions_async(db, current_user.id, limit=limit)
    return CreditTransactionsResponse(
        transactions=[
            CreditTransactionRead(
                id=str(row.id),
                transaction_type=str(row.transaction_type.value if hasattr(row.transaction_type, "value") else row.transaction_type),
                amount=int(row.amount or 0),
                balance_after=int(row.balance_after or 0),
                source=row.source,
                source_id=row.source_id,
                description=row.description,
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
            for row in rows
        ]
    )


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase_credits(
    request: PurchaseRequest,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Purchase credits for current user.
    """
    _ = current_user
    _ = db
    package = get_package_by_id(request.package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    raise HTTPException(
        status_code=410,
        detail={
            "error": "credits_purchase_deprecated",
            "message": "Direct credit minting is disabled. Use /payments/checkout for hosted payment.",
        },
    )


@router.post("/deduct")
async def deduct_user_credits(
    amount: int = COST_PER_GENERATION,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Deduct credits (called internally during generation).
    Returns 402 if insufficient balance.
    """
    if amount <= 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_credit_amount",
                "message": "Credit deduction amount must be positive.",
            },
        )

    success = await deduct_credits_async(
        db,
        current_user.id,
        amount,
        transaction_type=CreditTransactionType.GENERATION_DEBIT,
        source="manual_api",
    )

    if not success:
        balance = await get_balance_async(db, current_user.id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": "Not enough credits. Please top up to continue.",
                "required": amount,
                "balance": balance,
            }
        )

    new_balance = await get_balance_async(db, current_user.id)
    return {
        "success": True,
        "deducted": amount,
        "new_balance": new_balance,
    }


@router.post("/add")
async def add_user_credits(
    request: AdminAddCreditsRequest,
    _: None = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
):
    """Add credits for a specific user (admin only)."""
    new_balance = await add_credits_async(
        db,
        request.user_id,
        request.amount,
        transaction_type=CreditTransactionType.ADMIN_GRANT if request.amount >= 0 else CreditTransactionType.ADMIN_DEDUCT,
        source="admin",
        description="Admin credit adjustment",
    )
    return {"success": True, "new_balance": new_balance, "user_id": request.user_id}
