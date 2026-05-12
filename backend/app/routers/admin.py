"""Admin API routes for dashboard and management."""

from typing import Any, List

from datetime import datetime, timedelta, timezone
import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.config import settings
from app.core.database import get_db
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.models.user_credit import UserCredit
from app.services.admin_service import (
    get_dashboard_stats,
    grant_credits_to_user,
    get_all_users,
)
from app.services.account_risk_service import get_account_risk_summary
from app.services.admin_audit_service import list_admin_audit_logs, log_admin_action
from app.services.analytics_reporting_service import (
    get_city_ranking,
    get_funnel_report,
    get_template_ranking,
)
from app.services.lead_crm_service import build_crm_payload, list_crm_push_history, push_leads_to_crm, query_leads_for_crm
from app.services.ops_alert_service import get_ops_alerts
from app.services.ops_config_service import get_ops_config, save_ops_config
from app.services.ops_monitoring_service import get_ops_monitoring_summary
from app.services.retention_service import apply_order_retention, cleanup_expired_orders, cleanup_expired_source_images
from app.services.email_service import get_email_diagnostics, list_email_logs, send_test_email
from app.services.template_service import get_template_by_id
from app.services.generation_service import generation_service
from app.services.schema_guard_service import ensure_user_account_columns
from app.core.task_queue import enqueue_generate_order
from app.worker_tasks import run_order_generation

router = APIRouter(dependencies=[Depends(require_admin_token)])


class DashboardStats(BaseModel):
    """Dashboard statistics response."""
    total_orders: int
    total_revenue_credits: int
    estimated_revenue_usd: float
    total_users: int
    recent_users: int = 0
    recent_orders: int = 0
    total_revenue_cents: int = 0
    recent_revenue_cents: int = 0
    active_users_24h: int
    total_credits_in_circulation: int
    template_breakdown: dict
    recent_activity: list
    active_subscriptions: int = 0
    past_due_subscriptions: int = 0
    canceled_this_month: int = 0
    subscription_mrr_cents: int = 0
    credits_granted_this_month: int = 0
    recent_failed_payment_events: list[dict[str, Any]] = []


class PaymentConfigSummary(BaseModel):
    """Safe payment configuration summary for launch checks."""
    payment_provider: str
    debug: bool
    creem_api_base_url: str
    creem_api_key_mode: str
    creem_webhook_secret_configured: bool
    subscription_billing_enabled: bool
    credit_products_configured: dict[str, bool]
    subscription_products_configured: dict[str, bool]
    frontend_base_url: str
    webhook_base_url: str


class CreemProductCheckItem(BaseModel):
    key: str
    configured: bool
    ok: bool
    http_status: int | None = None
    product_id_suffix: str | None = None
    name: str | None = None
    status: str | None = None
    price_cents: int | None = None
    error: str | None = None


class CreemProductCheckResponse(BaseModel):
    api_base_url: str
    api_key_mode: str
    all_ok: bool
    products: list[CreemProductCheckItem]


class CreemCheckoutProbeResponse(BaseModel):
    ok: bool
    http_status: int | None = None
    mode: str | None = None
    checkout_status: str | None = None
    checkout_id: str | None = None
    checkout_url_prefix: str | None = None
    error: str | None = None


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


class AdminMeResponse(BaseModel):
    actor: str
    admin_roles: list[str]
    entry_url: str
    remote_join_enabled: bool
    remote_join_session_store: str
    generation_execution_mode: str


class GenerationProbeRequest(BaseModel):
    image_url: str
    second_image_url: str | None = None
    template_id: str | None = None
    global_style_text: str | None = None
    scene_text: str | None = None
    outfit_text: str | None = None
    prompt_override: str | None = None
    remote_join: bool = False
    execute_inline: bool | None = None


class GenerationProbeResponse(BaseModel):
    ok: bool
    started: bool
    completed: bool
    execution_mode: str
    order_id: str | None = None
    status: str | None = None
    task_id: str | None = None
    template_id: str | None = None
    error_message: str | None = None
    preview_image_urls: dict[str, Any] | None = None
    final_image_urls: dict[str, Any] | None = None


class AdminUserItem(BaseModel):
    id: str
    user_id: str
    name: str
    username: str | None = None
    email: str | None = None
    status: str
    role: str
    balance: int = 0
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class AdminUsersResponse(BaseModel):
    users: list[AdminUserItem]
    total: int
    page: int
    page_size: int


class UpdateStatusRequest(BaseModel):
    status: str


class AdminOrderUser(BaseModel):
    id: str
    name: str
    email: str | None = None
    username: str | None = None


class AdminOrderItem(BaseModel):
    id: str
    order_no: str
    user: AdminOrderUser | None = None
    amount_cents: int | None = None
    amount_usd: float | None = None
    status: str
    template_id: str | None = None
    created_at: datetime | None = None
    paid_at: datetime | None = None


class AdminOrdersResponse(BaseModel):
    orders: list[AdminOrderItem]
    total: int
    page: int
    page_size: int


class AdminOrderDetail(AdminOrderItem):
    user_id: str
    style_template: str | None = None
    generation_params: dict[str, Any] | None = None
    source_image_urls: dict[str, Any] | None = None
    preview_image_urls: dict[str, Any] | None = None
    final_image_urls: dict[str, Any] | None = None
    payment_id: str | None = None
    task_id: str | None = None
    error_message: str | None = None
    storage_cleanup_status: str | None = None
    source_images_expires_at: datetime | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    updated_at: datetime | None = None


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


class CleanupAssetsResponse(BaseModel):
    source_images: dict[str, int]
    generated_assets: dict[str, int]


class AdminAuditLogItem(BaseModel):
    id: str
    actor: str
    action: str
    request_method: str | None = None
    request_path: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime


class TestEmailRequest(BaseModel):
    to: EmailStr


class EmailLogItem(BaseModel):
    id: str
    purpose: str
    provider: str
    to_email: str
    subject: str
    status: str
    provider_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None


USER_STATUS_VALUES = {"active", "disabled", "suspended", "blocked"}
ORDER_STATUS_VALUES = {status.value for status in OrderStatus}


def _pagination(page: int, page_size: int) -> tuple[int, int, int]:
    clean_page = max(1, int(page or 1))
    clean_page_size = max(1, min(100, int(page_size or 20)))
    return clean_page, clean_page_size, (clean_page - 1) * clean_page_size


def _user_display_name(user: User | None) -> str:
    if user is None:
        return "Unknown user"
    return (
        (user.nickname or "").strip()
        or (user.username or "").strip()
        or (user.email or "").strip()
        or (user.openid or "").strip()
        or str(user.id)
    )


def _user_item(user: User, balance: int | None = None) -> AdminUserItem:
    return AdminUserItem(
        id=str(user.id),
        user_id=user.openid or str(user.id),
        name=_user_display_name(user),
        username=user.username,
        email=user.email,
        status=user.status or "active",
        role=user.role or "user",
        balance=int(balance or 0),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _order_status_value(order: Order) -> str:
    return order.status.value if hasattr(order.status, "value") else str(order.status)


def _order_item(order: Order, user: User | None) -> AdminOrderItem:
    amount_cents = int(order.price_cents or 0)
    return AdminOrderItem(
        id=str(order.id),
        order_no=str(order.id),
        user=AdminOrderUser(
            id=str(user.id),
            name=_user_display_name(user),
            email=user.email,
            username=user.username,
        ) if user else None,
        amount_cents=amount_cents,
        amount_usd=round(amount_cents / 100, 2),
        status=_order_status_value(order),
        template_id=order.template_id,
        created_at=order.created_at,
        paid_at=order.paid_at,
    )


def _order_detail(order: Order, user: User | None) -> AdminOrderDetail:
    base = _order_item(order, user).model_dump()
    return AdminOrderDetail(
        **base,
        user_id=str(order.user_id),
        style_template=order.style_template,
        generation_params=order.generation_params,
        source_image_urls=order.source_image_urls,
        preview_image_urls=order.preview_image_urls,
        final_image_urls=order.final_image_urls,
        payment_id=order.payment_id,
        task_id=order.task_id,
        error_message=order.error_message,
        storage_cleanup_status=order.storage_cleanup_status,
        source_images_expires_at=order.source_images_expires_at,
        expires_at=order.expires_at,
        deleted_at=order.deleted_at,
        updated_at=order.updated_at,
    )


async def _get_admin_user(db: AsyncSession, user_id: str) -> User:
    try:
        user_uuid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid user ID")
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _get_admin_order(db: AsyncSession, order_id: str) -> Order:
    try:
        order_uuid = uuid.UUID(str(order_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid order ID")
    result = await db.execute(select(Order).where(Order.id == order_uuid))
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _validate_public_image_url(value: str, *, field_name: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail=f"{field_name} is required")
    try:
        parsed = httpx.URL(raw)
    except Exception:
        raise HTTPException(status_code=422, detail=f"{field_name} must be an http(s) URL")
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise HTTPException(status_code=422, detail=f"{field_name} must be an http(s) URL")
    return raw


async def _get_or_create_generation_probe_user(db: AsyncSession) -> User:
    await ensure_user_account_columns(db)
    result = await db.execute(select(User).where(User.openid == "admin_generation_probe"))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        openid="admin_generation_probe",
        nickname="Admin Generation Probe",
        role="user",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


def _probe_response(order: Order, *, execution_mode: str, started: bool) -> GenerationProbeResponse:
    status_value = _order_status_value(order)
    completed = status_value == OrderStatus.COMPLETED.value
    error_message = order.error_message
    return GenerationProbeResponse(
        ok=bool(started and (completed or not error_message)),
        started=started,
        completed=completed,
        execution_mode=execution_mode,
        order_id=str(order.id),
        status=status_value,
        task_id=order.task_id,
        template_id=order.template_id,
        error_message=error_message,
        preview_image_urls=order.preview_image_urls,
        final_image_urls=order.final_image_urls,
    )


@router.get("/me", response_model=AdminMeResponse)
async def get_admin_me(request: Request):
    """Return the currently accepted admin session and operator-facing entry details."""
    return AdminMeResponse(
        actor=str(getattr(request.state, "admin_actor", "unknown-admin")),
        admin_roles=["owner", "admin", "operator"],
        entry_url="/admin",
        remote_join_enabled=bool(settings.remote_join_enabled),
        remote_join_session_store="redis_with_database_persistence",
        generation_execution_mode=settings.generation_execution_mode,
    )


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """
    Get admin dashboard statistics.
    Includes order counts, revenue, user stats, and recent activity.
    """
    stats = await get_dashboard_stats(db)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    stats["recent_users"] = int(
        await db.scalar(select(func.count(User.id)).where(User.created_at >= since)) or 0
    )
    stats["recent_orders"] = int(
        await db.scalar(select(func.count(Order.id)).where(Order.created_at >= since)) or 0
    )
    stats["total_revenue_cents"] = int(
        await db.scalar(select(func.coalesce(func.sum(Order.price_cents), 0))) or 0
    )
    stats["recent_revenue_cents"] = int(
        await db.scalar(
            select(func.coalesce(func.sum(Order.price_cents), 0)).where(Order.created_at >= since)
        )
        or 0
    )
    return DashboardStats(**stats)


@router.get("/payment_config_summary", response_model=PaymentConfigSummary)
async def get_payment_config_summary():
    """Return non-secret payment config metadata for production verification."""
    api_key = (settings.creem_api_key or "").strip()
    if api_key.startswith("creem_test_"):
        api_key_mode = "test"
    elif api_key.startswith("creem_"):
        api_key_mode = "live"
    elif api_key:
        api_key_mode = "unknown"
    else:
        api_key_mode = "missing"

    return PaymentConfigSummary(
        payment_provider=settings.payment_mode,
        debug=bool(settings.debug),
        creem_api_base_url=(settings.creem_api_base_url or "").rstrip("/"),
        creem_api_key_mode=api_key_mode,
        creem_webhook_secret_configured=bool((settings.creem_webhook_secret or "").strip()),
        subscription_billing_enabled=bool(settings.subscription_billing_enabled),
        credit_products_configured={
            "pack_50": bool((settings.creem_product_pack_50 or "").strip()),
            "pack_120": bool((settings.creem_product_pack_120 or "").strip()),
            "pack_300": bool((settings.creem_product_pack_300 or "").strip()),
        },
        subscription_products_configured={
            "starter_monthly": bool((settings.creem_subscription_starter_product_id or "").strip()),
            "creator_monthly": bool((settings.creem_subscription_creator_product_id or "").strip()),
            "studio_monthly": bool((settings.creem_subscription_studio_product_id or "").strip()),
        },
        frontend_base_url=settings.effective_frontend_base_url,
        webhook_base_url=settings.effective_webhook_base_url,
    )


@router.get("/creem_product_check", response_model=CreemProductCheckResponse)
async def check_creem_products():
    """Verify configured Creem products using the configured API key."""
    api_key = (settings.creem_api_key or "").strip()
    if api_key.startswith("creem_test_"):
        api_key_mode = "test"
    elif api_key.startswith("creem_"):
        api_key_mode = "live"
    elif api_key:
        api_key_mode = "unknown"
    else:
        api_key_mode = "missing"

    base_url = (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")
    configured_products = [
        ("pack_50", settings.creem_product_pack_50),
        ("pack_120", settings.creem_product_pack_120),
        ("pack_300", settings.creem_product_pack_300),
        ("starter_monthly", settings.creem_subscription_starter_product_id),
        ("creator_monthly", settings.creem_subscription_creator_product_id),
        ("studio_monthly", settings.creem_subscription_studio_product_id),
    ]

    if not api_key:
        return CreemProductCheckResponse(
            api_base_url=base_url,
            api_key_mode=api_key_mode,
            all_ok=False,
            products=[
                CreemProductCheckItem(key=key, configured=bool(product_id), ok=False, error="creem_api_key_missing")
                for key, product_id in configured_products
            ],
        )

    items: list[CreemProductCheckItem] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, raw_product_id in configured_products:
            product_id = str(raw_product_id or "").strip()
            suffix = product_id[-8:] if product_id else None
            if not product_id:
                items.append(
                    CreemProductCheckItem(
                        key=key,
                        configured=False,
                        ok=False,
                        product_id_suffix=suffix,
                        error="product_id_missing",
                    )
                )
                continue

            try:
                response = await client.get(
                    f"{base_url}/v1/products",
                    params={"product_id": product_id},
                    headers={"x-api-key": api_key, "Accept": "application/json"},
                )
                payload = response.json() if response.content else {}
                product = payload.get("product") if isinstance(payload, dict) and isinstance(payload.get("product"), dict) else payload
                name = product.get("name") or product.get("title") if isinstance(product, dict) else None
                status_value = product.get("status") if isinstance(product, dict) else None
                price_value = product.get("price") or product.get("amount") if isinstance(product, dict) else None
                items.append(
                    CreemProductCheckItem(
                        key=key,
                        configured=True,
                        ok=response.status_code == 200 and product_id in response.text,
                        http_status=response.status_code,
                        product_id_suffix=suffix,
                        name=str(name) if name else None,
                        status=str(status_value) if status_value else None,
                        price_cents=int(price_value) if isinstance(price_value, int) else None,
                        error=None if response.status_code == 200 else response.text[:240],
                    )
                )
            except Exception as exc:  # pragma: no cover - network diagnostics only
                items.append(
                    CreemProductCheckItem(
                        key=key,
                        configured=True,
                        ok=False,
                        product_id_suffix=suffix,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

    return CreemProductCheckResponse(
        api_base_url=base_url,
        api_key_mode=api_key_mode,
        all_ok=all(item.ok for item in items),
        products=items,
    )


@router.post("/creem_checkout_probe", response_model=CreemCheckoutProbeResponse)
async def probe_creem_checkout():
    """Create a provider-only checkout session to validate outbound live checkout."""
    api_key = (settings.creem_api_key or "").strip()
    product_id = (settings.creem_product_pack_50 or "").strip()
    if not api_key or not product_id:
        return CreemCheckoutProbeResponse(ok=False, error="creem_api_key_or_product_missing")

    base_url = (settings.creem_api_base_url or "https://api.creem.io").rstrip("/")
    payload = {
        "product_id": product_id,
        "request_id": f"launch_probe_{uuid.uuid4().hex[:16]}",
        "success_url": f"{settings.effective_frontend_base_url.rstrip('/')}/?payment=success&probe=1",
        "metadata": {
            "source": "admin_launch_probe",
            "package_id": "pack_50",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{base_url}/v1/checkouts",
                json=payload,
                headers={"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            )
        data = response.json() if response.content else {}
    except Exception as exc:  # pragma: no cover - network diagnostics only
        return CreemCheckoutProbeResponse(ok=False, error=f"{type(exc).__name__}: {exc}")

    checkout_url = str(data.get("checkout_url") or "")
    return CreemCheckoutProbeResponse(
        ok=response.status_code == 200 and bool(checkout_url),
        http_status=response.status_code,
        mode=str(data.get("mode")) if data.get("mode") else None,
        checkout_status=str(data.get("status")) if data.get("status") else None,
        checkout_id=str(data.get("id")) if data.get("id") else None,
        checkout_url_prefix=checkout_url[:32] if checkout_url else None,
        error=None if response.status_code == 200 else response.text[:240],
    )


@router.post("/generation_probe", response_model=GenerationProbeResponse)
async def probe_generation(
    payload: GenerationProbeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Start a real admin-only image generation probe without charging a customer."""
    image_url = _validate_public_image_url(payload.image_url, field_name="image_url")
    images = [image_url]
    if payload.second_image_url and payload.second_image_url.strip():
        images.append(_validate_public_image_url(payload.second_image_url, field_name="second_image_url"))

    is_couple = len(images) >= 2
    if payload.remote_join and not is_couple:
        raise HTTPException(status_code=422, detail="remote_join probes require second_image_url")
    if payload.remote_join and not settings.remote_join_enabled:
        raise HTTPException(status_code=409, detail="Remote join is disabled in runtime configuration")

    template_id = (payload.template_id or "").strip() or ("royal_castle" if is_couple else "solo_royal_castle")
    template = get_template_by_id(template_id)
    if template is None:
        raise HTTPException(status_code=422, detail="Unknown template_id")

    try:
        generation_service.validate_runtime_requirements(force=True)
    except Exception as exc:
        await log_admin_action(
            db,
            action="generation_probe",
            request=request,
            details={
                "template_id": template_id,
                "started": False,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return GenerationProbeResponse(
            ok=False,
            started=False,
            completed=False,
            execution_mode=settings.generation_execution_mode,
            template_id=template_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    probe_user = await _get_or_create_generation_probe_user(db)
    couple_flow = "remote" if payload.remote_join and is_couple else ("local" if is_couple else None)
    execution_mode = "inline" if (
        settings.using_inline_generation_execution if payload.execute_inline is None else bool(payload.execute_inline)
    ) else "arq"

    order = Order(
        user_id=probe_user.id,
        status=OrderStatus.CHECKING,
        template_id=template_id,
        style_template=template_id,
        source_image_urls={"images": images},
        generation_params={
            "admin_probe": True,
            "admin_actor": str(getattr(request.state, "admin_actor", "unknown-admin")),
            "credits_cost": 0,
            "access_tier": "admin_probe",
            "download_locked": False,
            "gatekeeper": {"passed": True, "skipped": True, "reason": "admin_generation_probe"},
            "content_policy": {"passed": True, "skipped": True, "reason": "admin_generation_probe"},
            "remote_join": bool(payload.remote_join),
            "couple_flow": couple_flow,
            "subject_count": len(images),
            "director_mode": False,
            "global_style_text": (payload.global_style_text or "admin production probe").strip(),
            "scene_text": (payload.scene_text or "").strip() or None,
            "outfit_text": (payload.outfit_text or "").strip() or None,
            "prompt_override": (payload.prompt_override or "").strip() or None,
            "probe_created_at": datetime.now(timezone.utc).isoformat(),
        },
        price_cents=0,
    )
    apply_order_retention(order, plan_code=None, has_paid_credits=True)
    db.add(order)
    await db.flush()
    await db.commit()
    await db.refresh(order)

    started = False
    if execution_mode == "inline":
        params = order.generation_params if isinstance(order.generation_params, dict) else {}
        task_id = f"admin-probe-inline-{order.id}"
        order.generation_params = {**params, "execution_mode": "inline", "queue_job_id": task_id}
        order.task_id = task_id
        order.status = OrderStatus.GENERATING
        await db.commit()
        started = True
        await run_order_generation(str(order.id))
    else:
        try:
            queue_job_id = await enqueue_generate_order(str(order.id))
            started = True
            params = order.generation_params if isinstance(order.generation_params, dict) else {}
            order.generation_params = {**params, "execution_mode": "arq", "queue_job_id": queue_job_id}
            order.task_id = queue_job_id
            order.status = OrderStatus.GENERATING
            await db.commit()
        except Exception as exc:
            order.status = OrderStatus.CREATED
            order.error_message = f"queue_unavailable: {exc}"
            await db.commit()

    refreshed = (await db.execute(select(Order).where(Order.id == order.id))).scalar_one()
    response = _probe_response(refreshed, execution_mode=execution_mode, started=started)
    await log_admin_action(
        db,
        action="generation_probe",
        request=request,
        details={
            "order_id": str(refreshed.id),
            "template_id": template_id,
            "execution_mode": execution_mode,
            "started": response.started,
            "completed": response.completed,
            "ok": response.ok,
        },
    )
    return response


@router.post("/grant_credits", response_model=GrantCreditsResponse)
async def grant_credits(
    request: Request,
    payload: GrantCreditsRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Grant credits to a user (admin operation).
    """
    if not payload.user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required")

    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    if payload.amount > 10000:
        raise HTTPException(status_code=400, detail="Amount too large (max 10000)")
    
    result = await grant_credits_to_user(db, payload.user_id, payload.amount)
    await log_admin_action(
        db,
        action="grant_credits",
        request=request,
        details={"target_user_id": payload.user_id, "amount": payload.amount},
    )
    return GrantCreditsResponse(**result)


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List users for the admin table with search, status filter, and pagination."""
    await ensure_user_account_columns(db)
    clean_page, clean_page_size, offset = _pagination(page, page_size)
    filters = []
    clean_search = (search or "").strip()
    if clean_search:
        pattern = f"%{clean_search}%"
        filters.append(
            or_(
                User.nickname.ilike(pattern),
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                User.openid.ilike(pattern),
                cast(User.id, String).ilike(pattern),
            )
        )
    clean_status = (status or "").strip().lower()
    if clean_status:
        filters.append(User.status == clean_status)

    total = int(await db.scalar(select(func.count(User.id)).where(*filters)) or 0)
    rows = (
        await db.execute(
            select(User, func.coalesce(UserCredit.balance, 0).label("balance"))
            .outerjoin(UserCredit, UserCredit.user_id == User.id)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset(offset)
            .limit(clean_page_size)
        )
    ).all()

    return AdminUsersResponse(
        users=[_user_item(user, int(balance or 0)) for user, balance in rows],
        total=total,
        page=clean_page,
        page_size=clean_page_size,
    )


@router.patch("/users/{user_id}/status", response_model=AdminUserItem)
@router.post("/users/{user_id}/status", response_model=AdminUserItem)
async def update_user_status(
    user_id: str,
    payload: UpdateStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update only the operational status of a user."""
    await ensure_user_account_columns(db)
    clean_status = (payload.status or "").strip().lower()
    if clean_status not in USER_STATUS_VALUES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Allowed: {', '.join(sorted(USER_STATUS_VALUES))}")

    user = await _get_admin_user(db, user_id)
    previous_status = user.status
    user.status = clean_status
    await db.flush()
    await log_admin_action(
        db,
        action="update_user_status",
        request=request,
        details={"target_user_id": str(user.id), "from": previous_status, "to": clean_status},
    )

    balance = int(
        await db.scalar(select(func.coalesce(UserCredit.balance, 0)).where(UserCredit.user_id == user.id))
        or 0
    )
    return _user_item(user, balance)


@router.get("/orders", response_model=AdminOrdersResponse)
async def list_admin_orders(
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List orders with user information for admin operations."""
    await ensure_user_account_columns(db)
    clean_page, clean_page_size, offset = _pagination(page, page_size)
    filters = []
    clean_search = (search or "").strip()
    if clean_search:
        pattern = f"%{clean_search}%"
        filters.append(
            or_(
                cast(Order.id, String).ilike(pattern),
                cast(Order.user_id, String).ilike(pattern),
                User.nickname.ilike(pattern),
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                User.openid.ilike(pattern),
            )
        )
    clean_status = (status or "").strip().upper()
    if clean_status:
        if clean_status not in ORDER_STATUS_VALUES:
            raise HTTPException(status_code=422, detail=f"Invalid order status. Allowed: {', '.join(sorted(ORDER_STATUS_VALUES))}")
        filters.append(Order.status == clean_status)

    total = int(
        await db.scalar(
            select(func.count(Order.id))
            .select_from(Order)
            .join(User, User.id == Order.user_id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Order, User)
            .join(User, User.id == Order.user_id)
            .where(*filters)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(clean_page_size)
        )
    ).all()
    for order, _user in rows:
        if order.status == OrderStatus.GENERATING:
            await generation_service.refresh_order(str(order.id))
            await db.refresh(order)

    return AdminOrdersResponse(
        orders=[_order_item(order, user) for order, user in rows],
        total=total,
        page=clean_page,
        page_size=clean_page_size,
    )


@router.get("/orders/{order_id}", response_model=AdminOrderDetail)
async def get_admin_order_detail(
    order_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return an admin-safe order detail payload."""
    await ensure_user_account_columns(db)
    await generation_service.refresh_order(order_id)
    order = await _get_admin_order(db, order_id)
    user = await db.get(User, order.user_id)
    return _order_detail(order, user)


@router.patch("/orders/{order_id}/status", response_model=AdminOrderDetail)
@router.post("/orders/{order_id}/status", response_model=AdminOrderDetail)
async def update_order_status(
    order_id: str,
    payload: UpdateStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update an order status using the existing OrderStatus enum."""
    await ensure_user_account_columns(db)
    clean_status = (payload.status or "").strip().upper()
    if clean_status not in ORDER_STATUS_VALUES:
        raise HTTPException(status_code=422, detail=f"Invalid order status. Allowed: {', '.join(sorted(ORDER_STATUS_VALUES))}")

    order = await _get_admin_order(db, order_id)
    previous_status = _order_status_value(order)
    order.status = OrderStatus(clean_status)
    await db.flush()
    await log_admin_action(
        db,
        action="update_order_status",
        request=request,
        details={"order_id": str(order.id), "from": previous_status, "to": clean_status},
    )

    user = await db.get(User, order.user_id)
    return _order_detail(order, user)


@router.post("/cleanup_expired_assets", response_model=CleanupAssetsResponse)
async def cleanup_admin_expired_assets(
    request: Request,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Delete expired source images and generated assets according to retention policy."""
    source_images = await cleanup_expired_source_images(db, limit=limit)
    generated_assets = await cleanup_expired_orders(db, limit=limit)
    await log_admin_action(
        db,
        action="cleanup_expired_assets",
        request=request,
        details={"limit": limit, "source_images": source_images, "generated_assets": generated_assets},
    )
    return CleanupAssetsResponse(source_images=source_images, generated_assets=generated_assets)


@router.get("/ops_config", response_model=OpsConfigResponse)
async def get_admin_ops_config():
    """Get operator-managed config for templates, pricing, and placements."""
    return OpsConfigResponse(**get_ops_config())


@router.put("/ops_config", response_model=OpsConfigResponse)
async def update_admin_ops_config(
    payload: OpsConfigResponse,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update operator-managed config without code deploy."""
    saved = save_ops_config(payload.model_dump())
    await log_admin_action(db, action="update_ops_config", request=request, details={"sections": list(saved.keys())})
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


@router.get("/email_diagnostics")
async def get_admin_email_diagnostics():
    """Return non-secret production email and DNS diagnostics."""
    return get_email_diagnostics()


@router.post("/email_test")
async def post_admin_email_test(
    payload: TestEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a real test email and record the delivery attempt."""
    result = await send_test_email(
        to=str(payload.to),
        db=db,
        metadata={"source": "admin_email_test"},
    )
    await log_admin_action(
        db,
        action="send_test_email",
        request=request,
        details={"to_domain": str(payload.to).rsplit("@", 1)[-1], "sent": bool(result.get("sent"))},
    )
    return result


@router.get("/email_logs", response_model=list[EmailLogItem])
async def get_admin_email_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Return recent outbound email attempts."""
    return [
        EmailLogItem(
            id=str(row.id),
            purpose=row.purpose,
            provider=row.provider,
            to_email=row.to_email,
            subject=row.subject,
            status=row.status,
            provider_message_id=row.provider_message_id,
            error_code=row.error_code,
            error_message=row.error_message,
            metadata=row.metadata_json,
            created_at=row.created_at,
        )
        for row in await list_email_logs(db, limit=limit)
    ]


@router.get("/risk_overview")
async def get_admin_risk_overview(
    days: int = 7,
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
):
    """Return signup, verification, and starter-credit abuse monitoring summary."""
    return await get_account_risk_summary(db, days=days, limit=limit)


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
    request: Request,
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
    await log_admin_action(
        db,
        action="crm_push",
        request=request,
        details={"limit": limit, "city": city, "source_page": source_page, "source_slot": source_slot, "template_id": template_id, "pushed": result.get("pushed")},
    )
    return CrmPushResponse(**result)


@router.get("/crm_push_history", response_model=list[CrmPushHistoryItem])
async def get_admin_crm_push_history(limit: int = 20):
    """Return recent CRM push audit records."""
    return [CrmPushHistoryItem(**item) for item in list_crm_push_history(limit=limit)]


@router.get("/audit_logs", response_model=list[AdminAuditLogItem])
async def get_admin_audit_logs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Return recent privileged admin operations."""
    logs = await list_admin_audit_logs(db, limit=limit)
    return [
        AdminAuditLogItem(
            id=str(item.id),
            actor=item.actor,
            action=item.action,
            request_method=item.request_method,
            request_path=item.request_path,
            ip_address=item.ip_address,
            user_agent=item.user_agent,
            details=item.details,
            created_at=item.created_at,
        )
        for item in logs
    ]
