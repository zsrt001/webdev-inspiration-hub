"""Recommendation API routes (M2 local studio picks)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import Lead
from app.services.local_reco_service import recommend_local_studios
from app.services.ops_config_service import get_recommendation_config


def _raise_recommendations_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "local_recommendations_retired",
            "message": "Local vendor recommendations are not part of this release.",
        },
    )


router = APIRouter(dependencies=[Depends(_raise_recommendations_retired)])


class LocalStudioRecoResponse(BaseModel):
    id: str
    name: str
    city: str
    tags: list[str] = Field(default_factory=list)
    service_modes: list[str] = Field(default_factory=list)
    highlight: str | None = None
    match_reason: str | None = None
    ranking_factors: list[str] = Field(default_factory=list)
    score: int | None = None
    lead_count: int = 0
    cta_label: str = "Contact"
    cta_type: str = "copy"  # copy | url
    cta_value: str | None = None


def _parse_notes_meta(notes: str | None) -> dict[str, str]:
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


async def _recent_reco_lead_counts(db: AsyncSession, *, days: int) -> dict[str, int]:
    since = datetime.utcnow() - timedelta(days=max(7, min(365, int(days))))
    result = await db.execute(select(Lead.notes).where(Lead.created_at >= since))
    counter: Counter[str] = Counter()
    for notes in result.scalars().all():
        reco_id = _parse_notes_meta(notes).get("source_reco_id", "").strip()
        if reco_id:
            counter[reco_id] += 1
    return dict(counter)


@router.get("/local_studios", response_model=list[LocalStudioRecoResponse])
async def list_local_studios(
    city: str | None = Query(default=None, max_length=40),
    wedding_date: str | None = Query(default=None, max_length=20),
    template_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=3, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
) -> list[LocalStudioRecoResponse]:
    """
    Get localized studio recommendations.

    Query params:
    - city: free-form city input (best-effort match)
    - wedding_date: ISO date string (optional, reserved for ranking)
    """
    _raise_recommendations_retired()
    recommendation_config = get_recommendation_config()
    lead_counts = await _recent_reco_lead_counts(
        db,
        days=int(recommendation_config.get("lead_lookback_days") or 90),
    )
    recos = recommend_local_studios(
        city=city,
        wedding_date=wedding_date,
        template_id=template_id,
        limit=limit,
        lead_counts=lead_counts,
        lead_boost_per_conversion=int(recommendation_config.get("lead_boost_per_conversion") or 0),
        lead_boost_cap=int(recommendation_config.get("lead_boost_cap") or 0),
        manual_boosts=recommendation_config.get("manual_boosts") or {},
    )
    return [
        LocalStudioRecoResponse(
            id=r.id,
            name=r.name,
            city=r.city,
            tags=list(r.tags or []),
            service_modes=list(r.service_modes or []),
            highlight=r.highlight,
            match_reason=r.match_reason,
            ranking_factors=list(r.ranking_factors or []),
            score=int(r.score),
            lead_count=int(r.lead_count or 0),
            cta_label=r.cta_label,
            cta_type=r.cta_type,
            cta_value=r.cta_value,
        )
        for r in recos
    ]
