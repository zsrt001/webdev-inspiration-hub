"""Leads API routes (commercial lead capture)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.database import get_db
from app.core.phone_crypto import decrypt_phone, encrypt_phone
from app.models.lead import Lead

router = APIRouter(prefix="/leads", tags=["leads"])


def _mask_phone(phone: str) -> str:
    text = (phone or "").strip()
    if len(text) <= 7:
        return text
    return f"{text[:3]}****{text[-4:]}"


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


class LeadSubmitRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=3, max_length=30)
    city: str = Field(min_length=1, max_length=100)
    privacy_accepted: bool = False
    wedding_date: str | None = None
    notes: str | None = None
    source_page: str | None = None
    source_reco_id: str | None = None
    source_reco_name: str | None = None
    source_slot: str | None = None
    template_id: str | None = None
    order_id: str | None = None


class LeadSubmitResponse(BaseModel):
    success: bool
    lead_id: str
    created_at: datetime


class LeadItem(BaseModel):
    id: str
    name: str
    phone: str
    city: str
    created_at: datetime
    notes: str | None = None
    meta: dict[str, str] | None = None


class LeadsListResponse(BaseModel):
    leads: list[LeadItem]
    total: int


@router.post("/submit", response_model=LeadSubmitResponse)
async def submit_lead(request: LeadSubmitRequest, db: AsyncSession = Depends(get_db)):
    if not request.privacy_accepted:
        raise HTTPException(status_code=422, detail="Privacy Policy and Terms of Service must be accepted")
    notes_parts: list[str] = []
    if request.wedding_date:
        notes_parts.append(f"wedding_date={request.wedding_date}")
    if request.source_page:
        notes_parts.append(f"source_page={request.source_page}")
    if request.source_slot:
        notes_parts.append(f"source_slot={request.source_slot}")
    if request.source_reco_id:
        notes_parts.append(f"source_reco_id={request.source_reco_id}")
    if request.source_reco_name:
        notes_parts.append(f"source_reco_name={request.source_reco_name}")
    if request.template_id:
        notes_parts.append(f"template_id={request.template_id}")
    if request.order_id:
        notes_parts.append(f"order_id={request.order_id}")
    if request.notes:
        notes_parts.append(request.notes)
    notes = " | ".join(notes_parts) if notes_parts else None

    lead = Lead(
        name=request.name.strip(),
        phone=encrypt_phone(request.phone.strip()),
        city=request.city.strip(),
        notes=notes,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return LeadSubmitResponse(success=True, lead_id=str(lead.id), created_at=lead.created_at)


@router.get("/list", response_model=LeadsListResponse)
async def list_leads(
    limit: int = 100,
    city: str | None = None,
    source_page: str | None = None,
    source_slot: str | None = None,
    template_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_token),
):
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
    leads = result.scalars().all()
    return LeadsListResponse(
        leads=[
            LeadItem(
                id=str(l.id),
                name=l.name,
                phone=_mask_phone(decrypt_phone(l.phone)),
                city=l.city,
                created_at=l.created_at,
                notes=l.notes,
                meta=_parse_notes_meta(l.notes),
            )
            for l in leads
        ],
        total=len(leads),
    )


@router.get("/export.csv")
async def export_leads_csv(
    city: str | None = None,
    source_page: str | None = None,
    source_slot: str | None = None,
    template_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_token),
):
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
    result = await db.execute(stmt.order_by(Lead.created_at.desc()))
    leads = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "name",
        "phone",
        "city",
        "created_at",
        "source_page",
        "source_slot",
        "source_reco_id",
        "source_reco_name",
        "template_id",
        "order_id",
        "notes",
    ])
    for l in leads:
        meta = _parse_notes_meta(l.notes)
        writer.writerow(
            [
                str(l.id),
                l.name,
                decrypt_phone(l.phone),
                l.city,
                l.created_at.isoformat(),
                meta.get("source_page", ""),
                meta.get("source_slot", ""),
                meta.get("source_reco_id", ""),
                meta.get("source_reco_name", ""),
                meta.get("template_id", ""),
                meta.get("order_id", ""),
                l.notes or "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
