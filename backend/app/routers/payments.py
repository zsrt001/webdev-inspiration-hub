"""Hosted payment routes."""

from __future__ import annotations

from html import escape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.user_auth import get_request_user
from app.models.user import User
from app.services.payment_service import payment_service, PaymentError
from app.services.feature_flag_service import require_request_capability

router = APIRouter()


class CheckoutRequest(BaseModel):
    package_id: str
    return_url: str | None = None


class CheckoutResponse(BaseModel):
    purchase_id: str
    provider: str
    status: str
    checkout_url: str


class PaymentStatusResponse(BaseModel):
    purchase_id: str
    provider: str
    package_id: str
    status: str
    completed: bool
    checkout_url: str | None = None
    credits_added: int
    balance: int
    message: str


class ManualReviewRequest(BaseModel):
    purchase_id: str
    reason: str | None = None


def _raise_payment_error(exc: PaymentError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    )


def _append_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v})
    return urlunparse(parsed._replace(query=urlencode(query), fragment=parsed.fragment))


def _payment_message(provider: str, status_value: str, *, submitted: bool = False) -> str:
    if status_value == "paid":
        return "Payment completed."
    if status_value == "failed":
        return "Payment failed."
    if status_value == "expired":
        return "Payment expired."
    if status_value == "refunded":
        return "Payment refunded."
    if provider == "manual_review":
        if submitted:
            return "Payment submitted and is pending manual confirmation."
        return "Checkout created. Complete payment and submit the reference for review."
    return "Payment is pending."


def _render_manual_checkout_html(context: dict) -> str:
    purchase = context["purchase"]
    metadata = purchase.metadata_json if isinstance(purchase.metadata_json, dict) else {}
    package_label = str(metadata.get("package_label") or purchase.package_id)
    amount = f"${(int(purchase.price_cents or 0) / 100):.2f}"
    purchase_id = str(purchase.id)
    token = str(purchase.provider_request_id or "")
    return_url = str(context.get("return_url") or "")
    display_name = escape(str(context.get("display_name") or "Manual Review Checkout"))
    instructions = escape(str(context.get("instructions") or ""))
    contact = escape(str(context.get("contact") or ""))
    submitted_at = str(context.get("submitted_at") or "").strip()
    status_value = payment_service._status_value(purchase.status)

    if status_value == "paid":
        return_href = _append_query(return_url, payment="success", purchase_id=purchase_id)
        action_block = f'<a class="primary" href="{escape(return_href)}">Return to site</a>'
    elif status_value in {"failed", "expired", "refunded"}:
        return_href = _append_query(return_url, payment="failed", purchase_id=purchase_id)
        action_block = f'<a class="primary" href="{escape(return_href)}">Return to site</a>'
    else:
        cancel_href = _append_query(return_url, payment="canceled", purchase_id=purchase_id)
        submitted_note = (
            f"<p class=\"meta\">Submitted: {escape(submitted_at)}</p>" if submitted_at else ""
        )
        action_block = (
            f"{submitted_note}"
            f"<form method=\"post\" action=\"/api/v1/payments/manual/submit\">"
            f"<input type=\"hidden\" name=\"purchase_id\" value=\"{escape(purchase_id)}\" />"
            f"<input type=\"hidden\" name=\"token\" value=\"{escape(token)}\" />"
            f"<button class=\"primary\" type=\"submit\">I have completed payment</button>"
            f"</form>"
            f"<a class=\"secondary\" href=\"{escape(cancel_href)}\">Cancel and return</a>"
        )

    contact_block = f"<p class=\"meta\">Contact: {contact}</p>" if contact else ""
    instructions_block = f"<p>{instructions}</p>" if instructions else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{display_name}</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(135deg, #f8f3eb, #f5efe7);
      color: #1f2937;
    }}
    .wrap {{
      max-width: 680px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .card {{
      background: #fff;
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 28px;
    }}
    .badge {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #efe2c6;
      color: #7a5b1e;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .summary {{
      margin: 20px 0;
      padding: 16px;
      border-radius: 14px;
      background: #f8fafc;
    }}
    .summary strong {{
      display: block;
      font-size: 24px;
      margin-top: 6px;
    }}
    form {{
      margin-top: 18px;
    }}
    .primary, .secondary {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 46px;
      border-radius: 12px;
      text-decoration: none;
      font-weight: 700;
      margin-top: 12px;
      width: 100%;
      box-sizing: border-box;
    }}
    .primary {{
      border: 0;
      background: #111827;
      color: #fff;
      cursor: pointer;
    }}
    .secondary {{
      border: 1px solid #d1d5db;
      color: #374151;
      background: #fff;
    }}
    .meta {{
      color: #6b7280;
      font-size: 14px;
    }}
    code {{
      display: block;
      margin-top: 8px;
      padding: 12px;
      border-radius: 12px;
      background: #111827;
      color: #f9fafb;
      overflow-wrap: anywhere;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="badge">{escape(status_value)}</div>
      <h1>{display_name}</h1>
      <p>Complete the payment offline or via customer support, then submit this order for manual review.</p>
      <div class="summary">
        <div>Package</div>
        <strong>{escape(package_label)}</strong>
        <div class="meta">Amount: {escape(amount)} | Credits: {int(purchase.credits or 0)}</div>
      </div>
      <p class="meta">Purchase reference</p>
      <code>{escape(purchase_id)}</code>
      {contact_block}
      {instructions_block}
      {action_block}
    </div>
  </div>
</body>
</html>"""


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    request: CheckoutRequest,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.CREDIT_PACK_CHECKOUT)
    try:
        purchase = await payment_service.create_checkout(
            db,
            user=current_user,
            package_id=request.package_id,
            return_url=request.return_url,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)

    return CheckoutResponse(
        purchase_id=str(purchase.id),
        provider=purchase.provider,
        status=payment_service._status_value(purchase.status),
        checkout_url=purchase.checkout_url or "",
    )


@router.get("/status/{purchase_id}", response_model=PaymentStatusResponse)
async def get_payment_status(
    purchase_id: str,
    checkout_id: str | None = Query(default=None),
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        purchase, balance = await payment_service.get_purchase_status(
            db,
            purchase_id=purchase_id,
            user_id=current_user.id,
            checkout_id=checkout_id,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)

    status_value = payment_service._status_value(purchase.status)
    completed = status_value == "paid"
    metadata = purchase.metadata_json if isinstance(purchase.metadata_json, dict) else {}
    message = _payment_message(
        purchase.provider,
        status_value,
        submitted=bool(metadata.get("manual_submitted_at")),
    )

    return PaymentStatusResponse(
        purchase_id=str(purchase.id),
        provider=purchase.provider,
        package_id=purchase.package_id,
        status=status_value,
        completed=completed,
        checkout_url=purchase.checkout_url,
        credits_added=int(purchase.credits if completed else 0),
        balance=balance,
        message=message,
    )


@router.get("/manual/checkout", response_class=HTMLResponse)
async def manual_checkout_page(
    purchase_id: str = Query(...),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.CREDIT_PACK_CHECKOUT)
    try:
        context = await payment_service.get_manual_checkout_context(
            db,
            purchase_id=purchase_id,
            token=token,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)
    return HTMLResponse(_render_manual_checkout_html(context))


@router.post("/manual/submit")
async def submit_manual_checkout(
    purchase_id: str = Form(...),
    token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.CREDIT_PACK_CHECKOUT)
    try:
        purchase = await payment_service.acknowledge_manual_checkout(
            db,
            purchase_id=purchase_id,
            token=token,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)

    metadata = purchase.metadata_json if isinstance(purchase.metadata_json, dict) else {}
    return_url = str(metadata.get("return_url") or "")
    redirect_url = _append_query(return_url, payment="pending_review", purchase_id=str(purchase.id))
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/manual/admin/complete")
async def complete_manual_checkout(
    request: ManualReviewRequest,
    _: None = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.CREDIT_PACK_CHECKOUT)
    try:
        purchase = await payment_service.complete_manual_purchase(
            db,
            purchase_id=request.purchase_id,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)
    return {
        "success": True,
        "purchase_id": str(purchase.id),
        "status": payment_service._status_value(purchase.status),
        "provider": purchase.provider,
    }


@router.post("/manual/admin/fail")
async def fail_manual_checkout(
    request: ManualReviewRequest,
    _: None = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
):
    await require_request_capability(None, db, Capability.CREDIT_PACK_CHECKOUT)
    try:
        purchase = await payment_service.fail_manual_purchase(
            db,
            purchase_id=request.purchase_id,
            reason=request.reason,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)
    return {
        "success": True,
        "purchase_id": str(purchase.id),
        "status": payment_service._status_value(purchase.status),
        "provider": purchase.provider,
    }


@router.post("/webhook/creem")
async def creem_webhook(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await http_request.body()
    signature = http_request.headers.get("creem-signature")
    payload = await http_request.json()
    try:
        purchase = await payment_service.process_webhook_event(
            db,
            payload=payload,
            body=body,
            signature_header=signature,
        )
    except PaymentError as exc:
        _raise_payment_error(exc)

    return {
        "success": True,
        "purchase_id": str(purchase.id) if purchase else None,
    }
