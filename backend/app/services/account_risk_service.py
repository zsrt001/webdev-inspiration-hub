"""Signup, verification, and starter-credit abuse monitoring."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_risk_event import AccountRiskEvent
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.user import User


NEW_ACCOUNT_EVENT_TYPES = (
    "google_register_created",
)

def _client_ip(request: Request | None) -> str:
    if request is None:
        return "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _device_key(request: Request | None) -> str:
    if request is None:
        return "unknown"
    explicit = (
        request.headers.get("x-device-id")
        or request.headers.get("x-client-fingerprint")
        or ""
    ).strip()
    if explicit:
        return explicit[:128]
    user_agent = (request.headers.get("user-agent") or "unknown")[:256]
    return f"{_client_ip(request)}:{user_agent}"


def _hash(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _email_domain(email: str | None) -> str | None:
    normalized = str(email or "").strip().lower()
    if "@" not in normalized:
        return None
    return normalized.rsplit("@", 1)[-1][:255]


async def record_account_risk_event(
    db: AsyncSession,
    *,
    event_type: str,
    request: Request | None = None,
    user: User | None = None,
    email: str | None = None,
    provider: str | None = None,
    risk_score: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AccountRiskEvent(
            user_id=user.id if user and user.id else None,
            event_type=str(event_type)[:64],
            provider=str(provider or "")[:32] or None,
            ip_hash=_hash(_client_ip(request)),
            device_hash=_hash(_device_key(request)),
            email_hash=_hash(email or getattr(user, "email", None)),
            email_domain=_email_domain(email or getattr(user, "email", None)),
            risk_score=max(0, min(100, int(risk_score or 0))),
            metadata_json=metadata,
        )
    )
    await db.flush()


async def check_new_account_risk_limits(
    db: AsyncSession,
    *,
    request: Request,
    ip_limit: int,
    device_limit: int,
    window_seconds: int = 3600,
) -> dict[str, Any] | None:
    """Return the first DB-backed new-account limit hit, if any."""
    since = datetime.now(timezone.utc) - timedelta(seconds=max(60, int(window_seconds or 3600)))
    ip_hash = _hash(_client_ip(request))
    device_hash = _hash(_device_key(request))

    if ip_hash:
        ip_count = int(
            await db.scalar(
                select(func.count(AccountRiskEvent.id)).where(
                    AccountRiskEvent.created_at >= since,
                    AccountRiskEvent.event_type.in_(NEW_ACCOUNT_EVENT_TYPES),
                    AccountRiskEvent.ip_hash == ip_hash,
                )
            )
            or 0
        )
        if ip_count >= max(1, int(ip_limit or 1)):
            return {"scope": "ip", "count": ip_count, "limit": int(ip_limit), "ip_hash": ip_hash}

    if device_hash:
        device_count = int(
            await db.scalar(
                select(func.count(AccountRiskEvent.id)).where(
                    AccountRiskEvent.created_at >= since,
                    AccountRiskEvent.event_type.in_(NEW_ACCOUNT_EVENT_TYPES),
                    AccountRiskEvent.device_hash == device_hash,
                )
            )
            or 0
        )
        if device_count >= max(1, int(device_limit or 1)):
            return {"scope": "device", "count": device_count, "limit": int(device_limit), "device_hash": device_hash}

    return None


async def get_account_risk_summary(db: AsyncSession, *, days: int = 7, limit: int = 12) -> dict[str, Any]:
    clean_days = max(1, min(90, int(days or 7)))
    clean_limit = max(1, min(50, int(limit or 12)))
    since = datetime.now(timezone.utc) - timedelta(days=clean_days)

    total_events = int(
        await db.scalar(select(func.count(AccountRiskEvent.id)).where(AccountRiskEvent.created_at >= since)) or 0
    )
    welcome_bonus_count = int(
        await db.scalar(
            select(func.count(CreditTransaction.id)).where(
                CreditTransaction.created_at >= since,
                CreditTransaction.transaction_type == CreditTransactionType.WELCOME_BONUS,
            )
        )
        or 0
    )
    blocked_events = int(
        await db.scalar(
            select(func.count(AccountRiskEvent.id)).where(
                AccountRiskEvent.created_at >= since,
                AccountRiskEvent.event_type.ilike("%blocked%"),
            )
        )
        or 0
    )
    high_risk_events = int(
        await db.scalar(
            select(func.count(AccountRiskEvent.id)).where(
                AccountRiskEvent.created_at >= since,
                AccountRiskEvent.risk_score >= 50,
            )
        )
        or 0
    )

    async def top_by(column) -> list[dict[str, Any]]:
        count_label = func.count(AccountRiskEvent.id).label("event_count")
        rows = (
            await db.execute(
                select(column.label("key"), count_label)
                .where(AccountRiskEvent.created_at >= since, column.is_not(None))
                .group_by(column)
                .order_by(count_label.desc())
                .limit(clean_limit)
            )
        ).all()
        return [{"key": str(key), "count": int(count)} for key, count in rows]

    recent_rows = (
        await db.execute(
            select(AccountRiskEvent)
            .order_by(AccountRiskEvent.created_at.desc(), AccountRiskEvent.id.desc())
            .limit(clean_limit)
        )
    ).scalars().all()

    return {
        "window_days": clean_days,
        "total_events": total_events,
        "welcome_bonus_count": welcome_bonus_count,
        "blocked_events": blocked_events,
        "high_risk_events": high_risk_events,
        "top_ip_hashes": await top_by(AccountRiskEvent.ip_hash),
        "top_device_hashes": await top_by(AccountRiskEvent.device_hash),
        "top_email_domains": await top_by(AccountRiskEvent.email_domain),
        "recent_events": [
            {
                "id": str(row.id),
                "user_id": str(row.user_id) if row.user_id else None,
                "event_type": row.event_type,
                "provider": row.provider,
                "ip_hash": row.ip_hash,
                "device_hash": row.device_hash,
                "email_domain": row.email_domain,
                "risk_score": int(row.risk_score or 0),
                "metadata": row.metadata_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in recent_rows
        ],
    }
