"""Admin API routes for dashboard and management."""

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.database import get_db
from app.services.admin_service import (
    get_dashboard_stats,
    grant_credits_to_user,
    get_all_users,
)
from app.services.analytics_reporting_service import (
    get_city_ranking,
    get_funnel_report,
    get_template_ranking,
)
from app.services.lead_crm_service import build_crm_payload, list_crm_push_history, push_leads_to_crm, query_leads_for_crm
from app.services.ops_alert_service import get_ops_alerts
from app.services.ops_config_service import get_ops_config, save_ops_config
from app.services.ops_monitoring_service import get_ops_monitoring_summary

router = APIRouter(dependencies=[Depends(require_admin_token)])


class DashboardStats(BaseModel):
    """Dashboard statistics response."""
    total_orders: int
    total_revenue_credits: int
    estimated_revenue_usd: float
    total_users: int
    active_users_24h: int
    total_credits_in_circulation: int
    template_breakdown: dict
    recent_activity: list


class GrantCreditsRequest(BaseModel):
    """Request to grant credits."""
    user_id: str
    amount: int


class GrantCreditsResponse(BaseModel):
    """Response for credit grant."""
    success: bool
    user_id: str
    credits_granted: int
    new_balance: int


class UserInfo(BaseModel):
    """User info model."""
    user_id: str
    balance: int


class UsersListResponse(BaseModel):
    """List of users response."""
    users: List[UserInfo]
    total: int


class OpsConfigResponse(BaseModel):
    template_overrides: dict[str, dict[str, Any]]
    pricing: dict[str, Any]
    placements: dict[str, Any]
    feature_flags: dict[str, Any]
    recommendations: dict[str, Any]
    crm: dict[str, Any]


class AnalyticsOverviewResponse(BaseModel):
    funnel: dict[str, Any]
    template_ranking: list[dict[str, Any]]
    city_ranking: list[dict[str, Any]]


class OpsOverviewResponse(BaseModel):
    runtime: dict[str, Any]
    orders: dict[str, int]
    live_portrait: dict[str, int]
    payments: dict[str, int]
    costs: list[dict[str, Any]]
    recent_failures: list[dict[str, Any]]


class OpsAlertResponse(BaseModel):
    level: str
    code: str
    title: str
    detail: str
    metric: dict[str, Any]


class CrmPushResponse(BaseModel):
    pushed: bool
    reason: str
    status_code: int | None = None
    payload: dict[str, Any]
    response_text: str | None = None


class CrmPushHistoryItem(BaseModel):
    created_at: str
    pushed: bool
    reason: str
    status_code: int | None = None
    count: int = 0
    filters: dict[str, Any] = {}


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """
    Get admin dashboard statistics.
    Includes order counts, revenue, user stats, and recent activity.
    """
    stats = await get_dashboard_stats(db)
    return DashboardStats(**stats)


@router.post("/grant_credits", response_model=GrantCreditsResponse)
async def grant_credits(request: GrantCreditsRequest, db: AsyncSession = Depends(get_db)):
    """
    Grant credits to a user (admin operation).
    """
    if not request.user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required")

    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    if request.amount > 10000:
        raise HTTPException(status_code=400, detail="Amount too large (max 10000)")
    
    result = await grant_credits_to_user(db, request.user_id, request.amount)
    return GrantCreditsResponse(**result)


@router.get("/users", response_model=UsersListResponse)
async def list_users(db: AsyncSession = Depends(get_db)):
    """
    Get list of all users with their credit balances.
    """
    users = await get_all_users(db)
    return UsersListResponse(
        users=[UserInfo(**u) for u in users],
        total=len(users)
    )


@router.get("/ops_config", response_model=OpsConfigResponse)
async def get_admin_ops_config():
    """Get operator-managed config for templates, pricing, and placements."""
    return OpsConfigResponse(**get_ops_config())


@router.put("/ops_config", response_model=OpsConfigResponse)
async def update_admin_ops_config(payload: OpsConfigResponse):
    """Update operator-managed config without code deploy."""
    saved = save_ops_config(payload.model_dump())
    return OpsConfigResponse(**saved)


@router.get("/analytics_overview", response_model=AnalyticsOverviewResponse)
async def get_admin_analytics_overview(
    days: int = 7,
    ranking_days: int = 30,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Return funnel, template ranking, and city ranking for operator review."""
    funnel = await get_funnel_report(db, days=days)
    template_ranking = await get_template_ranking(db, days=ranking_days, limit=limit)
    city_ranking = await get_city_ranking(db, days=ranking_days, limit=limit)
    return AnalyticsOverviewResponse(
        funnel=funnel,
        template_ranking=template_ranking,
        city_ranking=city_ranking,
    )


@router.get("/ops_overview", response_model=OpsOverviewResponse)
async def get_admin_ops_overview(
    days: int = 7,
    failure_limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Return runtime health, failure feed, and cost summary."""
    summary = await get_ops_monitoring_summary(db, days=days, failure_limit=failure_limit)
    return OpsOverviewResponse(**summary)


@router.get("/ops_alerts", response_model=list[OpsAlertResponse])
async def get_admin_ops_alerts(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    """Return derived operational alerts for the current monitoring window."""
    return [OpsAlertResponse(**item) for item in await get_ops_alerts(db, days=days)]


@router.get("/crm_preview")
async def get_admin_crm_preview(
    limit: int = 100,
    city: str | None = None,
    source_page: str | None = None,
    source_slot: str | None = None,
    template_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the JSON payload that would be sent to CRM."""
    leads = await query_leads_for_crm(
        db,
        limit=limit,
        city=city,
        source_page=source_page,
        source_slot=source_slot,
        template_id=template_id,
    )
    return build_crm_payload(leads)


@router.post("/crm_push", response_model=CrmPushResponse)
async def post_admin_crm_push(
    limit: int = 100,
    city: str | None = None,
    source_page: str | None = None,
    source_slot: str | None = None,
    template_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Push a filtered batch of leads to the configured CRM webhook."""
    result = await push_leads_to_crm(
        db,
        limit=limit,
        city=city,
        source_page=source_page,
        source_slot=source_slot,
        template_id=template_id,
    )
    return CrmPushResponse(**result)


@router.get("/crm_push_history", response_model=list[CrmPushHistoryItem])
async def get_admin_crm_push_history(limit: int = 20):
    """Return recent CRM push audit records."""
    return [CrmPushHistoryItem(**item) for item in list_crm_push_history(limit=limit)]
