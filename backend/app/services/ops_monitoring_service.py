from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.services.generation_service import generation_service
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus

settings = get_settings()


async def _runtime_checks() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "generation": {"ok": False, "detail": "not_checked"},
    }
    try:
        ok, detail = await generation_service.ping_runtime()
        summary["generation"] = {
            "ok": bool(ok),
            "detail": str(detail),
        }
    except Exception as exc:
        summary["generation"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    return summary


async def get_ops_monitoring_summary(db: AsyncSession, *, days: int = 7, failure_limit: int = 20) -> dict[str, Any]:
    days = max(1, min(30, int(days)))
    failure_limit = max(1, min(100, int(failure_limit)))
    start_day = date.today() - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)

    order_status_rows = (
        await db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status))
    ).all()
    live_status_rows = (
        await db.execute(select(LivePortraitJob.status, func.count(LivePortraitJob.id)).group_by(LivePortraitJob.status))
    ).all()
    payment_status_rows = (
        await db.execute(select(CreditPurchase.status, func.count(CreditPurchase.id)).group_by(CreditPurchase.status))
    ).all()

    cost_rows = defaultdict(
        lambda: {"day": "", "generation_credits": 0, "live_portrait_credits": 0, "payment_revenue_usd": 0.0}
    )

    ledger_cost_rows = (
        await db.execute(
            select(
                func.date(CreditTransaction.created_at),
                CreditTransaction.transaction_type,
                func.coalesce(func.sum(CreditTransaction.amount), 0),
            )
            .where(
                CreditTransaction.created_at >= start_dt,
                CreditTransaction.transaction_type.in_(
                    [
                        CreditTransactionType.GENERATION_DEBIT.value,
                        CreditTransactionType.GENERATION_REFUND.value,
                    ]
                ),
            )
            .group_by(
                func.date(CreditTransaction.created_at),
                CreditTransaction.transaction_type,
            )
        )
    ).all()
    for day_value, transaction_type, amount in ledger_cost_rows:
        bucket = cost_rows[day_value.isoformat()]
        bucket["day"] = day_value.isoformat()
        normalized_type = (
            transaction_type.value
            if hasattr(transaction_type, "value")
            else str(transaction_type)
        )
        if normalized_type == CreditTransactionType.GENERATION_DEBIT.value:
            bucket["generation_credits"] += max(0, -int(amount or 0))
        else:
            bucket["generation_credits"] = max(
                0,
                int(bucket["generation_credits"]) - max(0, int(amount or 0)),
            )

    failed_orders = (
        await db.execute(
            select(Order.id, Order.template_id, Order.error_message, Order.updated_at)
            .where(Order.error_message.is_not(None))
            .order_by(Order.updated_at.desc())
            .limit(failure_limit)
        )
    ).all()
    failed_live_jobs = (
        await db.execute(
            select(LivePortraitJob.id, LivePortraitJob.error_message, LivePortraitJob.updated_at)
            .where(LivePortraitJob.status == LivePortraitStatus.FAILED)
            .order_by(LivePortraitJob.updated_at.desc())
            .limit(failure_limit)
        )
    ).all()
    failed_payments = (
        await db.execute(
            select(CreditPurchase.id, CreditPurchase.last_error, CreditPurchase.updated_at)
            .where(CreditPurchase.status == CreditPurchaseStatus.FAILED)
            .order_by(CreditPurchase.updated_at.desc())
            .limit(failure_limit)
        )
    ).all()

    recent_failures: list[dict[str, Any]] = []
    for item_id, template_id, error_message, updated_at in failed_orders:
        recent_failures.append(
            {
                "kind": "order",
                "id": str(item_id),
                "scope": template_id or "",
                "error": error_message or "",
                "updated_at": updated_at.isoformat() if updated_at else "",
            }
        )
    for item_id, error_message, updated_at in failed_live_jobs:
        recent_failures.append(
            {
                "kind": "live_portrait",
                "id": str(item_id),
                "scope": "",
                "error": error_message or "",
                "updated_at": updated_at.isoformat() if updated_at else "",
            }
        )
    for item_id, error_message, updated_at in failed_payments:
        recent_failures.append(
            {
                "kind": "payment",
                "id": str(item_id),
                "scope": "",
                "error": error_message or "",
                "updated_at": updated_at.isoformat() if updated_at else "",
            }
        )
    recent_failures.sort(key=lambda item: item["updated_at"], reverse=True)

    return {
        "runtime": await _runtime_checks(),
        "orders": {
            str(status): int(count or 0) for status, count in order_status_rows
        },
        "live_portrait": {
            str(status): int(count or 0) for status, count in live_status_rows
        },
        "payments": {
            str(status): int(count or 0) for status, count in payment_status_rows
        },
        "costs": list(sorted(cost_rows.values(), key=lambda item: item["day"])),
        "recent_failures": recent_failures[:failure_limit],
    }
