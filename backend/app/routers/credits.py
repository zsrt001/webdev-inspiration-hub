"""Credits API routes for balance and payments."""

import logging
from typing import List, Literal

from asyncpg import PostgresError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.services.credit_service import (
    get_balance_async,
    list_credit_transactions_async,
    COST_PER_GENERATION,
)
from app.services.billing_catalog_service import (
    BillingCatalogUnavailable,
    load_active_catalog,
    localize_catalog_products,
)
from app.services.feature_flag_service import resolve_request_capability

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


def _raise_credit_catalog_unavailable() -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "code": "credit_catalog_unavailable",
            "message": "The authoritative credit catalog is not available yet.",
        },
    )


class BalanceResponse(BaseModel):
    """User balance response."""
    balance: int
    can_generate: bool
    cost_per_generation: int


class CreditPackage(BaseModel):
    """Credit package model."""
    id: str
    product_kind: Literal["credit_pack"] = "credit_pack"
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
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's credit balance."""
    balance = await get_balance_async(db, current_user.id)
    generation = await resolve_request_capability(
        db, Capability.GENERATION, verified_user_id=current_user.id
    )
    return BalanceResponse(
        balance=balance,
        can_generate=(generation.allowed and balance >= COST_PER_GENERATION),
        cost_per_generation=COST_PER_GENERATION,
    )


@router.get(
    "/packages",
    response_model=PackagesResponse,
)
async def list_packages(
    region: str | None = Query(default=None, max_length=8),
    locale: str | None = Query(default=None, max_length=16),
    db: AsyncSession = Depends(get_db),
):
    """Read the sole active, versioned PostgreSQL billing catalog."""
    _ = region
    if not callable(getattr(db, "scalars", None)):
        _raise_credit_catalog_unavailable()
    try:
        catalog = await load_active_catalog(
            db,
            environment=settings.runtime_environment,
        )
        credit_products = tuple(
            product
            for product in catalog.products
            if product.product_kind == "credit_pack"
        )
        localized = localize_catalog_products(credit_products, locale=locale)
    except (BillingCatalogUnavailable, PostgresError, SQLAlchemyError, OSError) as exc:
        logger.warning(
            "credit_catalog_unavailable exception_type=%s",
            type(exc).__name__,
        )
        _raise_credit_catalog_unavailable()

    return PackagesResponse(
        packages=[
            CreditPackage(
                id=item["product_code"],
                product_kind="credit_pack",
                credits=item["credits"],
                price=item["pre_tax_minor_units"] / 100,
                currency=item["currency"],
                price_cents=item["pre_tax_minor_units"],
                display_price=item["display_price"],
                region=item["region"],
                payment_methods=[],
                localized_pricing_ready=item["localized_pricing_ready"],
                label={
                    "pack_50": "Starter credits",
                    "pack_120": "Popular credits",
                    "pack_300": "Premium credits",
                }.get(item["product_code"], "Credits"),
                popular=item["product_code"] == "pack_120",
            )
            for item in localized
        ]
    )


@router.get("/transactions", response_model=CreditTransactionsResponse)
async def list_credit_transactions(
    current_user: User = Depends(get_session_user),
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
