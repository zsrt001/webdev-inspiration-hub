from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.click_stat import ClickStat
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.lead import Lead
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.order import Order, OrderStatus
from app.services.template_service import get_template_by_id


def _parse_lead_notes(notes: str | None) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not notes:
        return meta
    for part in notes.split(" | "):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            meta[key] = value
    return meta


def _daterange(days: int) -> list[date]:
    days = max(1, min(90, int(days)))
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)
    return [start_day + timedelta(days=index) for index in range(days)]


async def get_funnel_report(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    days_list = _daterange(days)
    start_day = days_list[0]
    start_dt = datetime.combine(start_day, datetime.min.time())

    daily: dict[str, dict[str, Any]] = {
        item.isoformat(): {
            "day": item.isoformat(),
            "template_clicks": 0,
            "reco_clicks": 0,
            "order_created": 0,
            "order_completed": 0,
            "payments_completed": 0,
            "payment_revenue_usd": 0.0,
            "leads_submitted": 0,
            "live_portrait_clicks": 0,
            "live_portrait_completed": 0,
        }
        for item in days_list
    }

    click_rows = (
        await db.execute(
            select(ClickStat.day, ClickStat.event_type, func.sum(ClickStat.count))
            .where(ClickStat.day >= start_day)
            .group_by(ClickStat.day, ClickStat.event_type)
        )
    ).all()
    for day_value, event_type, count in click_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        current = int(count or 0)
        if event_type == "template_click":
            bucket["template_clicks"] += current
        elif event_type in {"local_reco_banner", "local_reco_item", "vip_studio_banner"}:
            bucket["reco_clicks"] += current
        elif event_type == "live_portrait_click":
            bucket["live_portrait_clicks"] += current
        elif event_type == "live_portrait_completed":
            bucket["live_portrait_completed"] += current

    order_rows = (
        await db.execute(
            select(func.date(Order.created_at), Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt)
            .group_by(func.date(Order.created_at), Order.status)
        )
    ).all()
    for day_value, status, count in order_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        bucket["order_created"] += int(count or 0)
        if str(status) == OrderStatus.COMPLETED.value:
            bucket["order_completed"] += int(count or 0)

    payment_rows = (
        await db.execute(
            select(
                func.date(CreditPurchase.completed_at),
                func.count(CreditPurchase.id),
                func.coalesce(func.sum(CreditPurchase.price_cents), 0),
            )
            .where(
                CreditPurchase.status == CreditPurchaseStatus.PAID.value,
                CreditPurchase.completed_at.is_not(None),
                CreditPurchase.completed_at >= start_dt,
            )
            .group_by(func.date(CreditPurchase.completed_at))
        )
    ).all()
    for day_value, count, cents in payment_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        bucket["payments_completed"] += int(count or 0)
        bucket["payment_revenue_usd"] += round((int(cents or 0)) / 100.0, 2)

    lead_rows = (
        await db.execute(select(Lead.created_at).where(Lead.created_at >= start_dt))
    ).scalars().all()
    for created_at in lead_rows:
        if not created_at:
            continue
        bucket = daily.get(created_at.date().isoformat())
        if bucket:
            bucket["leads_submitted"] += 1

    live_rows = (
        await db.execute(
            select(func.date(LivePortraitJob.created_at), LivePortraitJob.status, func.count(LivePortraitJob.id))
            .where(LivePortraitJob.created_at >= start_dt)
            .group_by(func.date(LivePortraitJob.created_at), LivePortraitJob.status)
        )
    ).all()
    for day_value, status, count in live_rows:
        bucket = daily.get(day_value.isoformat())
        if not bucket:
            continue
        if str(status) == LivePortraitStatus.COMPLETED.value:
            bucket["live_portrait_completed"] += int(count or 0)

    rows = list(daily.values())
    totals = {
        "template_clicks": sum(row["template_clicks"] for row in rows),
        "reco_clicks": sum(row["reco_clicks"] for row in rows),
        "order_created": sum(row["order_created"] for row in rows),
        "order_completed": sum(row["order_completed"] for row in rows),
        "payments_completed": sum(row["payments_completed"] for row in rows),
        "payment_revenue_usd": round(sum(row["payment_revenue_usd"] for row in rows), 2),
        "leads_submitted": sum(row["leads_submitted"] for row in rows),
        "live_portrait_clicks": sum(row["live_portrait_clicks"] for row in rows),
        "live_portrait_completed": sum(row["live_portrait_completed"] for row in rows),
    }
    return {"days": len(days_list), "daily": rows, "totals": totals}


async def get_template_ranking(db: AsyncSession, *, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    days_list = _daterange(days)
    start_day = days_list[0]
    start_dt = datetime.combine(start_day, datetime.min.time())
    scores: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"template_id": "", "clicks": 0, "orders": 0, "completed_orders": 0, "leads": 0}
    )

    click_rows = (
        await db.execute(
            select(ClickStat.template_id, func.sum(ClickStat.count))
            .where(ClickStat.day >= start_day, ClickStat.event_type == "template_click", ClickStat.template_id != "na")
            .group_by(ClickStat.template_id)
        )
    ).all()
    for template_id, count in click_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["clicks"] += int(count or 0)

    order_rows = (
        await db.execute(
            select(Order.template_id, Order.status, func.count(Order.id))
            .where(Order.created_at >= start_dt, Order.template_id.is_not(None))
            .group_by(Order.template_id, Order.status)
        )
    ).all()
    for template_id, status, count in order_rows:
        key = (template_id or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        if str(status) == OrderStatus.COMPLETED.value:
            scores[key]["completed_orders"] += int(count or 0)
        else:
            scores[key]["orders"] += int(count or 0)

    lead_rows = (
        await db.execute(select(Lead.notes).where(Lead.created_at >= start_dt))
    ).scalars().all()
    for notes in lead_rows:
        meta = _parse_lead_notes(notes)
        key = (meta.get("template_id") or "").strip()
        if not key:
            continue
        scores[key]["template_id"] = key
        scores[key]["leads"] += 1

    rows: list[dict[str, Any]] = []
    for item in scores.values():
        template = get_template_by_id(item["template_id"])
        rows.append(
            {
                **item,
                "template_title": getattr(template, "marketing_title", None)
                or getattr(template, "title", None)
                or item["template_id"],
                "style_family": getattr(template, "style_family", None) or item["template_id"],
            }
        )

    rows.sort(key=lambda item: (-item["leads"], -item["completed_orders"], -item["orders"], -item["clicks"], item["template_id"]))
    return rows[: max(1, min(100, int(limit)))]


async def get_city_ranking(db: AsyncSession, *, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    days_list = _daterange(days)
    start_dt = datetime.combine(days_list[0], datetime.min.time())
    result = await db.execute(
        select(Lead.city, func.count(Lead.id))
        .where(Lead.created_at >= start_dt)
        .group_by(Lead.city)
        .order_by(func.count(Lead.id).desc(), Lead.city.asc())
        .limit(max(1, min(100, int(limit))))
    )
    return [{"city": city or "unknown", "leads": int(count or 0)} for city, count in result.all()]
