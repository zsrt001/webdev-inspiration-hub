"""Image retention and storage cleanup rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.order import Order
from app.services.storage import storage_service


SOURCE_IMAGE_RETENTION_DAYS = 7
FREE_ORDER_RETENTION_DAYS = 30
PAID_ORDER_RETENTION_DAYS = 90
SUBSCRIPTION_ORDER_RETENTION_DAYS = 180
STUDIO_ORDER_RETENTION_DAYS = 365


def source_image_retention_days() -> int:
    return SOURCE_IMAGE_RETENTION_DAYS


def order_retention_days(*, plan_code: str | None, has_paid_credits: bool) -> int:
    normalized_plan_code = str(plan_code or "").strip().lower()
    if normalized_plan_code == "studio_monthly":
        return STUDIO_ORDER_RETENTION_DAYS
    if normalized_plan_code:
        return SUBSCRIPTION_ORDER_RETENTION_DAYS
    if has_paid_credits:
        return PAID_ORDER_RETENTION_DAYS
    return FREE_ORDER_RETENTION_DAYS


async def user_has_paid_credit_history(db: AsyncSession, user_id) -> bool:
    result = await db.execute(
        select(CreditTransaction.id)
        .where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.transaction_type.in_(
                [
                    CreditTransactionType.PURCHASE,
                    CreditTransactionType.SUBSCRIPTION_GRANT,
                    CreditTransactionType.ADMIN_GRANT,
                ]
            ),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def apply_order_retention(
    order: Order,
    *,
    plan_code: str | None,
    has_paid_credits: bool,
    now: datetime | None = None,
) -> Order:
    base = now or datetime.now(timezone.utc)
    days = order_retention_days(plan_code=plan_code, has_paid_credits=has_paid_credits)
    order.source_images_expires_at = base + timedelta(days=SOURCE_IMAGE_RETENTION_DAYS)
    order.expires_at = base + timedelta(days=days)
    order.storage_cleanup_status = order.storage_cleanup_status or "active"
    return order


def _extract_urls(payload: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    urls: list[str] = []
    for value in payload.values():
        if isinstance(value, str) and value:
            urls.append(value)
        elif isinstance(value, list):
            urls.extend([str(item) for item in value if isinstance(item, str) and item])
        elif isinstance(value, dict):
            urls.extend(_extract_urls(value))
    return urls


def order_asset_urls(order: Order, *, include_source: bool = True, include_generated: bool = True) -> list[str]:
    urls: list[str] = []
    if include_source:
        urls.extend(_extract_urls(order.source_image_urls))
    if include_generated:
        urls.extend(_extract_urls(order.preview_image_urls))
        urls.extend(_extract_urls(order.final_image_urls))
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def delete_storage_urls(urls: Iterable[str]) -> dict[str, int]:
    deleted = 0
    failed = 0
    for url in urls:
        try:
            if storage_service.delete_file(str(url)):
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"deleted": deleted, "failed": failed}


async def cleanup_expired_source_images(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    cutoff = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(Order)
        .where(
            Order.deleted_at.is_(None),
            Order.source_images_expires_at.is_not(None),
            Order.source_images_expires_at <= cutoff,
            Order.source_image_urls.is_not(None),
        )
        .order_by(Order.source_images_expires_at.asc())
        .limit(max(1, min(500, int(limit))))
    )
    orders = list(result.scalars().all())
    deleted_files = 0
    failed_files = 0
    for order in orders:
        summary = delete_storage_urls(order_asset_urls(order, include_source=True, include_generated=False))
        deleted_files += summary["deleted"]
        failed_files += summary["failed"]
        order.source_image_urls = None
        order.storage_cleanup_status = "source_deleted" if summary["failed"] == 0 else "cleanup_failed"
    await db.flush()
    return {"orders": len(orders), "deleted_files": deleted_files, "failed_files": failed_files}


async def cleanup_expired_orders(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    cutoff = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(Order)
        .where(
            Order.deleted_at.is_(None),
            Order.expires_at.is_not(None),
            Order.expires_at <= cutoff,
        )
        .order_by(Order.expires_at.asc())
        .limit(max(1, min(500, int(limit))))
    )
    orders = list(result.scalars().all())
    deleted_files = 0
    failed_files = 0
    for order in orders:
        summary = delete_storage_urls(order_asset_urls(order))
        deleted_files += summary["deleted"]
        failed_files += summary["failed"]
        order.source_image_urls = None
        order.preview_image_urls = None
        order.final_image_urls = None
        order.deleted_at = cutoff
        order.storage_cleanup_status = "deleted" if summary["failed"] == 0 else "cleanup_failed"
    await db.flush()
    return {"orders": len(orders), "deleted_files": deleted_files, "failed_files": failed_files}
