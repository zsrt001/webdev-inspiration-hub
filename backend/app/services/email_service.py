"""Email notification service using Resend API."""

from __future__ import annotations

import html
import logging
import secrets
import time
from collections import defaultdict, deque
from email.utils import parseaddr
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_delivery_log import EmailDeliveryLog

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


def _normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def _email_domain(value: str) -> str:
    _, address = parseaddr(str(value or ""))
    normalized = _normalize_email(address or value)
    if "@" not in normalized:
        return ""
    return normalized.rsplit("@", 1)[-1]


async def _record_email_log(
    db: AsyncSession | None,
    *,
    to: str,
    subject: str,
    purpose: str,
    result: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    if db is None:
        return
    status_value = "sent" if result.get("sent") else "failed"
    error_code = str(result.get("reason") or result.get("status") or result.get("error_code") or "")[:64] or None
    error_message = str(result.get("error") or result.get("message") or "")[:2000] or None
    db.add(
        EmailDeliveryLog(
            purpose=purpose,
            provider="resend",
            to_email=_normalize_email(to),
            subject=str(subject)[:255],
            status=status_value,
            provider_message_id=str(result.get("id") or "")[:128] or None,
            error_code=error_code,
            error_message=error_message,
            metadata_json=metadata,
        )
    )
    await db.flush()


async def _send_email(
    *,
    to: str,
    subject: str,
    html: str,
    purpose: str = "notification",
    db: AsyncSession | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    api_key = (settings.resend_api_key or "").strip()
    if not api_key:
        logger.debug("Email skipped (no RESEND_API_KEY): %s -> %s", subject, to)
        result = {"sent": False, "reason": "no_api_key"}
        await _record_email_log(db, to=to, subject=subject, purpose=purpose, result=result, metadata=metadata)
        return result

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
            result = {"sent": True, "id": resp.json().get("id")}
            await _record_email_log(db, to=to, subject=subject, purpose=purpose, result=result, metadata=metadata)
            return result
        logger.warning("Resend API error %d: %s", resp.status_code, resp.text[:200])
        result = {"sent": False, "status": resp.status_code, "message": resp.text[:500]}
        await _record_email_log(db, to=to, subject=subject, purpose=purpose, result=result, metadata=metadata)
        return result
    except Exception as exc:
        logger.warning("Email send failed: %s", exc)
        result = {"sent": False, "error": str(exc)}
        await _record_email_log(db, to=to, subject=subject, purpose=purpose, result=result, metadata=metadata)
        return result


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
    return await _send_email(
        to=to,
        subject=f"Payment Confirmed - {int(credits)} Credits Added",
        html=html_body,
        purpose="payment_confirmation",
    )


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
    return await _send_email(to=to, subject="Your AI Wedding Photo is Ready", html=html_body, purpose="order_completed")


async def send_welcome_email(*, to: str, credits: int) -> dict[str, Any]:
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2>Welcome to AI Wedding Studio!</h2>
      <p>We've added <strong>{credits} free credits</strong> to your account so you can try our AI wedding photo generation.</p>
      <p>Upload your photo, pick a style, and see the magic happen.</p>
    </div>
    """
    return await _send_email(to=to, subject=f"Welcome! {credits} Starter Credits Added", html=html, purpose="welcome")


# ---------------------------------------------------------------------------
# Verification code public API
# ---------------------------------------------------------------------------

def is_verification_rate_limited(email: str) -> bool:
    return _code_store.is_rate_limited(email)


async def send_verification_code(
    email: str,
    *,
    db: AsyncSession | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    return await _send_email(
        to=email,
        subject=f"Your verification code: {code}",
        html=html_body,
        purpose="email_verification",
        db=db,
        metadata=metadata,
    )


def verify_email_code(email: str, code: str) -> bool:
    return _code_store.verify(email, code)


async def send_test_email(
    *,
    to: str,
    db: AsyncSession | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    html_body = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto">
      <h2>AI Wedding Studio email test</h2>
      <p>This message confirms that production email sending is reachable.</p>
      <p style="color:#666;font-size:13px">From: {html.escape(settings.email_from_address)}</p>
    </div>
    """
    return await _send_email(
        to=to,
        subject="AI Wedding Studio email test",
        html=html_body,
        purpose="admin_test",
        db=db,
        metadata=metadata,
    )


def _query_dns_records(name: str, record_type: str) -> list[str]:
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return []
    try:
        answers = dns.resolver.resolve(name, record_type, lifetime=4.0)
    except Exception:
        return []
    records: list[str] = []
    for answer in answers:
        if record_type.upper() == "TXT":
            records.append("".join(part.decode("utf-8", errors="ignore") for part in getattr(answer, "strings", [])))
        else:
            records.append(str(answer).rstrip("."))
    return records


def get_email_diagnostics() -> dict[str, Any]:
    settings = get_settings()
    api_key_configured = bool((settings.resend_api_key or "").strip())
    from_address = (settings.email_from_address or "").strip()
    domain = _email_domain(from_address)
    txt_records = _query_dns_records(domain, "TXT") if domain else []
    dmarc_records = _query_dns_records(f"_dmarc.{domain}", "TXT") if domain else []
    mx_records = _query_dns_records(domain, "MX") if domain else []

    return {
        "provider": "resend",
        "resend_api_key_configured": api_key_configured,
        "from_address": from_address,
        "from_domain": domain,
        "from_domain_usable": bool(domain and domain != "example.com"),
        "dns": {
            "spf_found": any(record.lower().startswith("v=spf1") for record in txt_records),
            "dmarc_found": any(record.lower().startswith("v=dmarc1") for record in dmarc_records),
            "mx_found": bool(mx_records),
            "txt_count": len(txt_records),
            "dmarc_count": len(dmarc_records),
            "mx_count": len(mx_records),
            "dns_checker": "dnspython" if _query_dns_records("example.com", "TXT") else "unavailable",
        },
        "ready": bool(api_key_configured and domain and domain != "example.com"),
        "notes": [
            "Verify the sending domain in Resend and keep SPF, DKIM, and DMARC green before production launch.",
            "MX is only required if this domain also receives mail; SPF/DKIM/DMARC affect deliverability.",
        ],
    }


async def list_email_logs(db: AsyncSession, *, limit: int = 50) -> list[EmailDeliveryLog]:
    clean_limit = max(1, min(200, int(limit or 50)))
    rows = (
        await db.execute(
            select(EmailDeliveryLog)
            .order_by(EmailDeliveryLog.created_at.desc(), EmailDeliveryLog.id.desc())
            .limit(clean_limit)
        )
    ).scalars().all()
    return list(rows)
