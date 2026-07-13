from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lead import Lead
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus
from app.services.ops_monitoring_service import get_ops_monitoring_summary

logger = logging.getLogger(__name__)


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

    since_utc = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    lead_since = since_utc.replace(tzinfo=None)
    lead_count = (
        await db.execute(select(func.count(Lead.id)).where(Lead.created_at >= lead_since))
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
            select(func.count(Order.id)).where(Order.updated_at >= since_utc, Order.error_message.is_not(None))
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
                LivePortraitJob.updated_at >= since_utc,
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


async def push_critical_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Push critical/warning alerts to external webhook (Slack/Feishu/DingTalk)."""
    settings = get_settings()
    webhook_url = (settings.ops_alert_webhook_url or "").strip()
    if not webhook_url:
        return {"pushed": False, "reason": "no_webhook_configured"}

    critical_alerts = [a for a in alerts if a["level"] in {"critical", "warning"}]
    if not critical_alerts:
        return {"pushed": False, "reason": "no_actionable_alerts"}

    lines = [f"[{a['level'].upper()}] {a['title']}: {a['detail']}" for a in critical_alerts]
    text = f"AI Wedding Studio Alerts ({len(critical_alerts)})\n" + "\n".join(lines)

    payload: dict[str, Any]
    if "hooks.slack.com" in webhook_url:
        payload = {"text": text}
    elif "feishu.cn" in webhook_url or "larksuite.com" in webhook_url:
        payload = {"msg_type": "text", "content": {"text": text}}
    elif "dingtalk.com" in webhook_url or "oapi.dingtalk.com" in webhook_url:
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        payload = {"text": text}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        return {"pushed": True, "status_code": resp.status_code, "alert_count": len(critical_alerts)}
    except Exception as exc:
        logger.warning("Alert webhook push failed: %s", exc)
        return {"pushed": False, "reason": f"webhook_error:{exc}"}
