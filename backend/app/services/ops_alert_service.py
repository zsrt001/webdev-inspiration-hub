from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus
from app.services.ops_monitoring_service import get_ops_monitoring_summary


def _alert(level: str, code: str, title: str, detail: str, metric: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "level": level,
        "code": code,
        "title": title,
        "detail": detail,
        "metric": metric or {},
    }


async def get_ops_alerts(db: AsyncSession, *, days: int = 7) -> list[dict[str, Any]]:
    summary = await get_ops_monitoring_summary(db, days=days, failure_limit=20)
    alerts: list[dict[str, Any]] = []

    runtime = summary.get("runtime") or {}
    for component, state in runtime.items():
        if not isinstance(state, dict):
            continue
        if not bool(state.get("ok")):
            alerts.append(
                _alert(
                    "critical",
                    f"{component}_down",
                    f"{component} unavailable",
                    str(state.get("detail") or "unhealthy"),
                )
            )

    orders = summary.get("orders") or {}
    order_pending = int(orders.get(OrderStatus.CHECKING.value, 0) or 0) + int(orders.get(OrderStatus.GENERATING.value, 0) or 0)
    if order_pending >= 20:
        alerts.append(
            _alert(
                "warning",
                "order_backlog_high",
                "Generation backlog is high",
                "Orders waiting in CHECKING/GENERATING exceeded threshold.",
                {"pending_orders": order_pending},
            )
        )

    live_portrait = summary.get("live_portrait") or {}
    live_pending = int(live_portrait.get(LivePortraitStatus.CREATED.value, 0) or 0) + int(live_portrait.get(LivePortraitStatus.GENERATING.value, 0) or 0)
    if live_pending >= 10:
        alerts.append(
            _alert(
                "warning",
                "live_portrait_backlog_high",
                "Live Portrait backlog is high",
                "Queued and processing motion jobs exceeded threshold.",
                {"pending_jobs": live_pending},
            )
        )

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    lead_count = (
        await db.execute(select(func.count(Lead.id)).where(Lead.created_at >= since))
    ).scalar_one()
    if int(lead_count or 0) == 0:
        alerts.append(
            _alert(
                "warning",
                "no_recent_leads",
                "No recent leads captured",
                f"No lead submissions were recorded in the last {days} days.",
                {"days": days},
            )
        )

    recent_failed_orders = (
        await db.execute(
            select(func.count(Order.id)).where(Order.updated_at >= since, Order.error_message.is_not(None))
        )
    ).scalar_one()
    if int(recent_failed_orders or 0) >= 5:
        alerts.append(
            _alert(
                "warning",
                "recent_order_failures_high",
                "Order failures need review",
                "Recent failed generations exceeded threshold.",
                {"recent_failed_orders": int(recent_failed_orders or 0)},
            )
        )

    recent_failed_live = (
        await db.execute(
            select(func.count(LivePortraitJob.id)).where(
                LivePortraitJob.updated_at >= since,
                LivePortraitJob.status == LivePortraitStatus.FAILED,
            )
        )
    ).scalar_one()
    if int(recent_failed_live or 0) >= 3:
        alerts.append(
            _alert(
                "warning",
                "recent_live_failures_high",
                "Live Portrait failures need review",
                "Recent failed motion jobs exceeded threshold.",
                {"recent_failed_live_jobs": int(recent_failed_live or 0)},
            )
        )

    if not alerts:
        alerts.append(
            _alert(
                "info",
                "ops_nominal",
                "Ops nominal",
                "No blocking alert is active for the current monitoring window.",
            )
        )

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda item: (severity_rank.get(item["level"], 99), item["code"]))
    return alerts
