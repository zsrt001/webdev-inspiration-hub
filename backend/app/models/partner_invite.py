"""Authenticated host/partner consent state for one immutable couple intent."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PartnerInviteStatus(StrEnum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    CONSENTED = "CONSENTED"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PartnerInvite(Base):
    __tablename__ = "partner_invites"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_partner_invites_token_hash"),
        UniqueConstraint("order_intent_id", name="uq_partner_invites_order_intent_id"),
        UniqueConstraint("order_id", name="uq_partner_invites_order_id"),
        UniqueConstraint("job_id", name="uq_partner_invites_job_id"),
        CheckConstraint(
            "status IN ('CREATED','ACCEPTED','CONSENTED','COMPLETED','REVOKED','EXPIRED','CANCELLED')",
            name="ck_partner_invites_status",
        ),
        CheckConstraint("purpose = 'COUPLE'", name="ck_partner_invites_purpose"),
        CheckConstraint("char_length(token_hash) = 64", name="ck_partner_invites_token_hash"),
        CheckConstraint("char_length(order_intent_hash) = 64", name="ck_partner_invites_intent_hash"),
        CheckConstraint("btrim(template_id) <> ''", name="ck_partner_invites_template_nonempty"),
        CheckConstraint("version >= 1", name="ck_partner_invites_version"),
        CheckConstraint(
            "partner_user_id IS NULL OR (partner_user_id <> host_user_id "
            "AND partner_identity_id <> host_identity_id)",
            name="ck_partner_invites_identity_distinct",
        ),
        CheckConstraint(
            "expires_at = created_at + interval '1 day'",
            name="ck_partner_invites_expiry_exact",
        ),
        CheckConstraint(
            "(partner_user_id IS NULL) = (partner_identity_id IS NULL)",
            name="ck_partner_invites_partner_binding_coherent",
        ),
        CheckConstraint(
            "(partner_asset_id IS NULL) = (partner_asset_sha256 IS NULL) "
            "AND (partner_asset_id IS NULL) = (consent_event_id IS NULL)",
            name="ck_partner_invites_consent_asset_coherent",
        ),
        CheckConstraint(
            "(order_id IS NULL) = (job_id IS NULL)",
            name="ck_partner_invites_order_binding_coherent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    host_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_identities.id", ondelete="RESTRICT"), nullable=False
    )
    partner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    partner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_identities.id", ondelete="RESTRICT"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="COUPLE")
    order_intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_intent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PartnerInviteStatus] = mapped_column(
        String(16), nullable=False, default=PartnerInviteStatus.CREATED
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    partner_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=True
    )
    partner_asset_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partner_invite_events.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
