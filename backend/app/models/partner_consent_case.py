"""Durable consent-withdrawal settlement and deletion lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PartnerConsentCaseStatus(StrEnum):
    OPEN = "OPEN"
    SETTLED_DELETION_PENDING = "SETTLED_DELETION_PENDING"
    CANCELLED_AND_DELETED = "CANCELLED_AND_DELETED"


class PartnerConsentCase(Base):
    __tablename__ = "partner_consent_cases"
    __table_args__ = (
        UniqueConstraint("invite_id", name="uq_partner_consent_cases_invite"),
        CheckConstraint(
            "status IN ('OPEN','SETTLED_DELETION_PENDING','CANCELLED_AND_DELETED')",
            name="ck_partner_consent_cases_status",
        ),
        CheckConstraint("version >= 1", name="ck_partner_consent_cases_version"),
        CheckConstraint(
            "owned_asset_ids IS NULL OR jsonb_typeof(owned_asset_ids) = 'array'",
            name="ck_partner_consent_cases_asset_ids_array",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partner_invites.id", ondelete="RESTRICT"), nullable=False
    )
    host_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    partner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[PartnerConsentCaseStatus] = mapped_column(
        String(32), nullable=False, default=PartnerConsentCaseStatus.OPEN
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    owned_asset_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    provider_cancel_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settlement_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
