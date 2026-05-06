"""Email notification service using Resend API."""

from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


async def _send_email(*, to: str, subject: str, html: str) -> dict[str, Any]:
    settings = get_settings()
    api_key = (settings.resend_api_key or "").strip()
    if not api_key:
        logger.debug("Email skipped (no RESEND_API_KEY): %s -> %s", subject, to)
        return {"sent": False, "reason": "no_api_key"}

    payload = {
        "from": f"{settings.email_from_name} <{settings.email_from_address}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in {200, 201}:
            return {"sent": True, "id": resp.json().get("id")}
        logger.warning("Resend API error %d: %s", resp.status_code, resp.text[:200])
        return {"sent": False, "status": resp.status_code}
    except Exception as exc:
        logger.warning("Email send failed: %s", exc)
        return {"sent": False, "error": str(exc)}


async def send_payment_confirmation(*, to: str, credits: int, package_name: str, amount_display: str) -> dict[str, Any]:
    safe_pkg = html.escape(str(package_name))
    safe_amount = html.escape(str(amount_display))
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2>Payment Confirmed</h2>
      <p>Thank you for your purchase!</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:8px 0;color:#666">Package</td><td style="padding:8px 0">{safe_pkg}</td></tr>
        <tr><td style="padding:8px 0;color:#666">Credits</td><td style="padding:8px 0">{int(credits)}</td></tr>
        <tr><td style="padding:8px 0;color:#666">Amount</td><td style="padding:8px 0">{safe_amount}</td></tr>
      </table>
      <p style="margin-top:24px;color:#666;font-size:13px">Your credits are ready to use. Start creating your AI wedding photos now!</p>
    </div>
    """
    return await _send_email(to=to, subject=f"Payment Confirmed - {int(credits)} Credits Added", html=html_body)


async def send_order_completed(*, to: str, order_id: str, preview_url: str | None = None) -> dict[str, Any]:
    safe_id = html.escape(str(order_id)[:8])
    preview_block = ""
    if preview_url:
        safe_url = html.escape(str(preview_url))
        preview_block = f'<p><img src="{safe_url}" style="max-width:100%;border-radius:8px" alt="preview"></p>'
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2>Your Photo is Ready!</h2>
      {preview_block}
      <p>Order <code>{safe_id}</code> has been completed. Log in to view and download your full-resolution photo.</p>
    </div>
    """
    return await _send_email(to=to, subject="Your AI Wedding Photo is Ready", html=html_body)


async def send_welcome_email(*, to: str, credits: int) -> dict[str, Any]:
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2>Welcome to AI Wedding Studio!</h2>
      <p>We've added <strong>{credits} free credits</strong> to your account so you can try our AI wedding photo generation.</p>
      <p>Upload your photo, pick a style, and see the magic happen.</p>
    </div>
    """
    return await _send_email(to=to, subject=f"Welcome! {credits} Free Credits Added", html=html)
