"""Email notification service using Resend API."""

from __future__ import annotations

import html
import logging
import secrets
import time
from collections import defaultdict, deque
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


# ---------------------------------------------------------------------------
# In-memory verification code store (serverless-safe for single instance)
# ---------------------------------------------------------------------------

class _VerificationCodeStore:
    """Stores verification codes with TTL and rate limiting."""

    MAX_VERIFY_ATTEMPTS = 5

    def __init__(self) -> None:
        self._codes: dict[str, tuple[str, float, int]] = {}  # email -> (code, expires_at, attempts)
        self._send_history: dict[str, deque[float]] = defaultdict(deque)

    def is_rate_limited(self, email: str) -> bool:
        settings = get_settings()
        history = self._send_history[email]
        cutoff = time.time() - 3600
        while history and history[0] < cutoff:
            history.popleft()
        return len(history) >= settings.verification_code_rate_limit

    def generate_and_store(self, email: str) -> str:
        settings = get_settings()
        code = f"{secrets.randbelow(900000) + 100000}"
        expires_at = time.time() + settings.verification_code_ttl_seconds
        normalized = email.strip().lower()
        self._codes[normalized] = (code, expires_at, 0)
        self._send_history[normalized].append(time.time())
        return code

    def verify(self, email: str, code: str) -> bool:
        normalized = email.strip().lower()
        stored = self._codes.get(normalized)
        if not stored:
            return False
        stored_code, expires_at, attempts = stored
        if time.time() > expires_at:
            del self._codes[normalized]
            return False
        if attempts >= self.MAX_VERIFY_ATTEMPTS:
            del self._codes[normalized]
            return False
        if stored_code != code.strip():
            self._codes[normalized] = (stored_code, expires_at, attempts + 1)
            return False
        del self._codes[normalized]
        return True


_code_store = _VerificationCodeStore()

# ---------------------------------------------------------------------------
# Disposable email domain blocklist
# ---------------------------------------------------------------------------

DISPOSABLE_EMAIL_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "tempmail.com",
    "throwaway.email", "yopmail.com", "sharklasers.com", "guerrillamailblock.com",
    "grr.la", "dispostable.com", "mailnesia.com", "maildrop.cc", "fakeinbox.com",
    "trashmail.com", "trashmail.net", "trashmail.me", "10minutemail.com",
    "temp-mail.org", "tempail.com", "mohmal.com", "getnada.com", "emailondeck.com",
    "33mail.com", "mailcatch.com", "discard.email", "discardmail.com",
    "spamgourmet.com", "mytemp.email", "burnermail.io", "inboxkitten.com",
    "jetable.org", "trash-mail.com", "harakirimail.com", "crazymailing.com",
    "mailforspam.com", "tempr.email", "tmail.ws", "tmpmail.net", "tmpmail.org",
    "bupmail.com", "mailnator.com", "spambox.us", "guerrillamail.info",
    "guerrillamail.biz", "guerrillamail.de", "guerrillamail.org",
}


def is_disposable_email(email: str) -> bool:
    domain = email.strip().lower().rsplit("@", 1)[-1]
    return domain in DISPOSABLE_EMAIL_DOMAINS


# ---------------------------------------------------------------------------
# IP-level rate limiter for verification sends (global, not per-email)
# ---------------------------------------------------------------------------

class _IPVerificationLimiter:
    """Limit total verification sends per IP to prevent mass farming."""

    def __init__(self, limit: int = 10, window_seconds: int = 3600) -> None:
        self._limit = limit
        self._window = window_seconds
        self._history: dict[str, deque[float]] = defaultdict(deque)

    def is_limited(self, ip: str) -> bool:
        history = self._history[ip]
        cutoff = time.time() - self._window
        while history and history[0] < cutoff:
            history.popleft()
        return len(history) >= self._limit

    def record(self, ip: str) -> None:
        self._history[ip].append(time.time())


_ip_verification_limiter = _IPVerificationLimiter(limit=10, window_seconds=3600)


def is_ip_verification_rate_limited(ip: str) -> bool:
    return _ip_verification_limiter.is_limited(ip)


def record_ip_verification(ip: str) -> None:
    _ip_verification_limiter.record(ip)


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


# ---------------------------------------------------------------------------
# Verification code public API
# ---------------------------------------------------------------------------

def is_verification_rate_limited(email: str) -> bool:
    return _code_store.is_rate_limited(email)


async def send_verification_code(email: str) -> dict[str, Any]:
    code = _code_store.generate_and_store(email)
    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2>Email Verification</h2>
      <p>Your verification code is:</p>
      <p style="font-size:32px;font-weight:bold;letter-spacing:4px;text-align:center;
         padding:16px;background:#f5f5f5;border-radius:8px">{code}</p>
      <p style="color:#666;font-size:13px">This code expires in 10 minutes. If you did not request this, please ignore.</p>
    </div>
    """
    return await _send_email(to=email, subject=f"Your verification code: {code}", html=html_body)


def verify_email_code(email: str, code: str) -> bool:
    return _code_store.verify(email, code)
