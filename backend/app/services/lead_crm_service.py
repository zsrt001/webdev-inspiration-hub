from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone_crypto import decrypt_phone
from app.models.lead import Lead
from app.services.ops_config_service import get_crm_config


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


def _history_file() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "data" / "crm_push_history.jsonl"


def _append_crm_push_history(record: dict[str, Any]) -> None:
    file_path = _history_file()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def list_crm_push_history(limit: int = 20) -> list[dict[str, Any]]:
    file_path = _history_file()
    if not file_path.exists():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
        if len(items) >= max(1, min(100, int(limit))):
            break
    return items


async def query_leads_for_crm(
    db: AsyncSession,
    *,
    limit: int = 100,
    city: str | None = None,
    source_page: str | None = None,
    source_slot: str | None = None,
    template_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Lead]:
    limit = max(1, min(500, int(limit)))
    stmt = select(Lead)
    if city:
        stmt = stmt.where(Lead.city.ilike(f"%{city.strip()}%"))
    if source_page:
        stmt = stmt.where(Lead.notes.ilike(f"%source_page={source_page.strip()}%"))
    if source_slot:
        stmt = stmt.where(Lead.notes.ilike(f"%source_slot={source_slot.strip()}%"))
    if template_id:
        stmt = stmt.where(Lead.notes.ilike(f"%template_id={template_id.strip()}%"))
    if date_from:
        stmt = stmt.where(Lead.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Lead.created_at <= datetime.combine(date_to, time.max))
    result = await db.execute(stmt.order_by(Lead.created_at.desc()).limit(limit))
    return list(result.scalars().all())


def serialize_lead_for_crm(lead: Lead) -> dict[str, Any]:
    meta = _parse_notes_meta(lead.notes)
    return {
        "lead_id": str(lead.id),
        "name": lead.name,
        "phone": decrypt_phone(lead.phone),
        "city": lead.city,
        "created_at": lead.created_at.isoformat() if lead.created_at else "",
        "wedding_date": meta.get("wedding_date", ""),
        "source_page": meta.get("source_page", ""),
        "source_slot": meta.get("source_slot", ""),
        "source_reco_id": meta.get("source_reco_id", ""),
        "source_reco_name": meta.get("source_reco_name", ""),
        "template_id": meta.get("template_id", ""),
        "order_id": meta.get("order_id", ""),
        "notes": lead.notes or "",
    }


def build_crm_payload(leads: list[Lead]) -> dict[str, Any]:
    crm = get_crm_config()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(leads),
        "batch_size": int(crm.get("batch_size") or 100),
        "items": [serialize_lead_for_crm(lead) for lead in leads],
    }


async def push_leads_to_crm(db: AsyncSession, **filters: Any) -> dict[str, Any]:
    crm = get_crm_config()
    if not bool(crm.get("enabled")):
        result = {
            "pushed": False,
            "reason": "crm_disabled",
            "payload": {"count": 0, "items": []},
        }
        _append_crm_push_history(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pushed": False,
                "reason": result["reason"],
                "status_code": None,
                "count": 0,
                "filters": {k: v for k, v in filters.items() if v not in (None, "", [])},
            }
        )
        return result

    webhook_url = str(crm.get("webhook_url") or "").strip()
    if not webhook_url:
        result = {
            "pushed": False,
            "reason": "crm_webhook_missing",
            "payload": {"count": 0, "items": []},
        }
        _append_crm_push_history(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pushed": False,
                "reason": result["reason"],
                "status_code": None,
                "count": 0,
                "filters": {k: v for k, v in filters.items() if v not in (None, "", [])},
            }
        )
        return result

    limit = filters.get("limit") or crm.get("batch_size") or 100
    leads = await query_leads_for_crm(db, limit=limit, city=filters.get("city"), source_page=filters.get("source_page"), source_slot=filters.get("source_slot"), template_id=filters.get("template_id"), date_from=filters.get("date_from"), date_to=filters.get("date_to"))
    payload = build_crm_payload(leads)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    auth_header_name = str(crm.get("auth_header_name") or "").strip()
    auth_header_value = str(crm.get("auth_header_value") or "").strip()
    if auth_header_name and auth_header_value:
        headers[auth_header_name] = auth_header_value

    response_text = ""
    status_code: int | None = None
    pushed = False
    reason = "unknown_error"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(webhook_url, json=payload, headers=headers)
        status_code = int(response.status_code)
        response_text = response.text[:1000]
        pushed = response.status_code < 400
        reason = "ok" if pushed else f"http_{response.status_code}"
    except Exception as exc:
        reason = f"{type(exc).__name__}"
        response_text = str(exc)[:1000]

    result = {
        "pushed": pushed,
        "status_code": status_code,
        "reason": reason,
        "payload": payload,
        "response_text": response_text,
    }
    _append_crm_push_history(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pushed": pushed,
            "reason": reason,
            "status_code": status_code,
            "count": int(payload.get("count") or 0),
            "filters": {k: v for k, v in filters.items() if v not in (None, "", [])},
        }
    )
    return result
