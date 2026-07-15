"""Image retention and storage cleanup rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.media_asset import MediaAssetStatus
from app.models.order import Order
from app.services.media_deletion_service import request_asset_deletion
from app.services.storage import storage_service


logger = logging.getLogger(__name__)


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
    """Return legacy URL references for migration diagnostics only."""
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


def _parse_asset_ids(values: list[str] | None) -> tuple[list[uuid.UUID], int]:
    parsed: list[uuid.UUID] = []
    invalid = 0
    seen: set[uuid.UUID] = set()
    for raw_value in values or []:
        try:
            asset_id = uuid.UUID(str(raw_value))
        except (TypeError, ValueError, AttributeError):
            invalid += 1
            continue
        if asset_id not in seen:
            seen.add(asset_id)
            parsed.append(asset_id)
    return parsed, invalid


async def _rollback_after_cleanup_error(db: AsyncSession) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        await rollback()


async def _request_retention_deletions(
    db: AsyncSession,
    asset_ids: list[uuid.UUID],
    *,
    reason: str,
    now: datetime,
) -> dict[str, int]:
    summary = {"deleted_assets": 0, "pending_assets": 0, "failed_assets": 0}
    for asset_id in asset_ids:
        try:
            result = await request_asset_deletion(
                db,
                asset_id,
                reason=reason,
                now=now,
            )
        except Exception:
            summary["failed_assets"] += 1
            await _rollback_after_cleanup_error(db)
            logger.exception("Retention deletion request failed for asset %s", asset_id)
            continue
        if MediaAssetStatus(result.asset.status) == MediaAssetStatus.DELETED:
            summary["deleted_assets"] += 1
        else:
            summary["pending_assets"] += 1
    return summary


def _merge_cleanup_counts(target: dict[str, int], update: dict[str, int]) -> None:
    for key in ("deleted_assets", "pending_assets", "failed_assets"):
        target[key] += int(update.get(key, 0))


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
            or_(
                Order.source_asset_ids.is_not(None),
                Order.source_image_urls.is_not(None),
            ),
        )
        .order_by(Order.source_images_expires_at.asc())
        .limit(max(1, min(500, int(limit))))
    )
    orders = list(result.scalars().all())
    summary = {
        "orders": len(orders),
        "deleted_assets": 0,
        "pending_assets": 0,
        "failed_assets": 0,
        "legacy_blocked_orders": 0,
    }
    for order in orders:
        asset_ids, invalid_ids = _parse_asset_ids(order.source_asset_ids)
        if order_asset_urls(order, include_source=True, include_generated=False):
            order.storage_cleanup_status = "legacy_reference_blocked"
            summary["legacy_blocked_orders"] += 1
            continue
        request_summary = await _request_retention_deletions(
            db,
            asset_ids,
            reason="retention_source",
            now=cutoff,
        )
        request_summary["failed_assets"] += invalid_ids
        _merge_cleanup_counts(summary, request_summary)
        if request_summary["failed_assets"]:
            order.storage_cleanup_status = "cleanup_failed"
        elif request_summary["pending_assets"]:
            order.storage_cleanup_status = "cleanup_pending"
        else:
            order.source_asset_ids = None
            order.storage_cleanup_status = "source_deleted"
    await db.flush()
    return summary


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
    summary = {
        "orders": len(orders),
        "deleted_assets": 0,
        "pending_assets": 0,
        "failed_assets": 0,
        "legacy_blocked_orders": 0,
    }
    for order in orders:
        asset_ids: list[uuid.UUID] = []
        invalid_ids = 0
        for raw_ids in (
            order.source_asset_ids,
            order.preview_asset_ids,
            order.final_asset_ids,
        ):
            parsed, invalid = _parse_asset_ids(raw_ids)
            asset_ids.extend(parsed)
            invalid_ids += invalid
        asset_ids = list(dict.fromkeys(asset_ids))
        if order_asset_urls(order):
            order.storage_cleanup_status = "legacy_reference_blocked"
            summary["legacy_blocked_orders"] += 1
            continue
        request_summary = await _request_retention_deletions(
            db,
            asset_ids,
            reason="retention_order",
            now=cutoff,
        )
        request_summary["failed_assets"] += invalid_ids
        _merge_cleanup_counts(summary, request_summary)
        if request_summary["failed_assets"]:
            order.storage_cleanup_status = "cleanup_failed"
        elif request_summary["pending_assets"]:
            order.storage_cleanup_status = "cleanup_pending"
        else:
            order.source_asset_ids = None
            order.preview_asset_ids = None
            order.final_asset_ids = None
            order.source_image_urls = None
            order.preview_image_urls = None
            order.final_image_urls = None
            order.deleted_at = cutoff
            order.storage_cleanup_status = "deleted"
    await db.flush()
    return summary


def cleanup_transient_generated_assets(
    *,
    now: datetime | None = None,
    older_than_hours: int = 6,
    limit: int = 200,
) -> dict:
    """Clean provider/intermediate generated/ files that are not customer delivery assets."""
    clean_hours = max(1, int(older_than_hours or 6))
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=clean_hours)
    return storage_service.cleanup_generated_files_older_than(
        cutoff=cutoff,
        limit=max(1, min(1000, int(limit or 200))),
    )
