"""Analytics API routes for ad tracking."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.models.click_stat import ClickStat

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()

# Best-effort in-memory fallback (DB is the source of truth in production).
click_counts: dict[str, int] = {}


def _allow_memory_fallback() -> bool:
    return settings.debug or settings.allow_memory_fallback


def _event_value(event: "ClickEvent") -> tuple[int, int]:
    meta = event.meta if isinstance(event.meta, dict) else {}
    if event.event_type == "asset_upload_completed":
        try:
            duration = int(float(meta.get("duration_ms") or 0))
        except Exception:
            duration = 0
        if duration <= 0:
            return 0, 0
        return min(duration, 30 * 60 * 1000), 1
    if event.event_type == "asset_upload_quality_scored":
        try:
            score = int(round(float(meta.get("quality_score") or 0)))
        except Exception:
            score = 0
        return max(0, min(100, score)), 1
    return 0, 0


class ClickEvent(BaseModel):
    """Click event data."""

    event_type: str  # e.g., "vip_studio_banner"
    source_page: str  # e.g., "preview"
    template_id: str | None = None
    meta: dict[str, Any] | None = None


class ClickResponse(BaseModel):
    """Response for click tracking."""

    success: bool
    total_clicks: int


@router.post("/click", response_model=ClickResponse)
async def track_click(event: ClickEvent, db: AsyncSession = Depends(get_db)) -> ClickResponse:
    """
    Track ad/banner click events.

    Stores daily aggregated counts in DB with a best-effort in-memory fallback.
    """
    event_type = (event.event_type or "").strip()[:80]
    source_page = (event.source_page or "").strip()[:80]
    template_id = ((event.template_id or "na").strip() or "na")[:64]
    key = f"{event_type}:{source_page}:{template_id}"
    value_sum, value_count = _event_value(event)

    # Keep in-memory fallback counters only when explicitly allowed.
    if _allow_memory_fallback():
        click_counts[key] = click_counts.get(key, 0) + 1

    logger.info(
        f"[Analytics] Click: {event_type} from {source_page} at {datetime.now().isoformat()} template_id={template_id} meta={event.meta}"
    )

    try:
        stmt = (
            insert(ClickStat)
            .values(
                day=date.today(),
                event_type=event_type,
                source_page=source_page,
                template_id=template_id,
                count=1,
                value_sum=value_sum,
                value_count=value_count,
            )
            .on_conflict_do_update(
                index_elements=["day", "event_type", "source_page", "template_id"],
                set_={
                    "count": ClickStat.count + 1,
                    "value_sum": ClickStat.value_sum + value_sum,
                    "value_count": ClickStat.value_count + value_count,
                },
            )
            .returning(ClickStat.count)
        )
        result = await db.execute(stmt)
        total_clicks = int(result.scalar_one())
        return ClickResponse(success=True, total_clicks=total_clicks)
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        if _allow_memory_fallback():
            logger.warning(f"[Analytics] DB unavailable, using memory fallback: {e}")
            return ClickResponse(success=True, total_clicks=click_counts.get(key, 0))
        logger.error(f"[Analytics] DB unavailable and memory fallback disabled: {e}")
        raise HTTPException(status_code=503, detail="analytics_store_unavailable")


@router.get("/stats")
async def get_stats(
    day: date | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_user),
):
    """Get analytics statistics (admin only in production)."""
    day = day or date.today()
    limit = max(1, min(1000, int(limit)))

    try:
        result = await db.execute(
            select(ClickStat)
            .where(ClickStat.day == day)
            .order_by(ClickStat.count.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        click_counts_db = {
            f"{r.event_type}:{r.source_page}:{r.template_id}": int(r.count) for r in rows
        }
        return {
            "day": day.isoformat(),
            "click_counts": click_counts_db,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        if _allow_memory_fallback():
            logger.warning(f"[Analytics] Stats DB unavailable, using memory fallback: {e}")
            return {
                "day": day.isoformat(),
                "click_counts": click_counts,
                "timestamp": datetime.now().isoformat(),
            }
        logger.error(f"[Analytics] Stats DB unavailable and memory fallback disabled: {e}")
        raise HTTPException(status_code=503, detail="analytics_store_unavailable")
