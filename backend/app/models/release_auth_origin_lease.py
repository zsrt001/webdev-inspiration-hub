"""Durable, CAS-protected Supabase callback lease for one Production release."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReleaseAuthOriginLease(Base):
    __tablename__ = "release_auth_origin_leases"
    __table_args__ = (
        UniqueConstraint(
            "release_activation_id", name="uq_release_auth_origin_lease_activation"
        ),
        UniqueConstraint("callback_url", name="uq_release_auth_origin_lease_callback"),
        CheckConstraint(
            "state IN ('SNAPSHOTTED','ADDED','REMOVED')",
            name="ck_release_auth_origin_lease_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_activation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("release_activations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_ref_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    callback_url: Mapped[str] = mapped_column(String(512), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    private_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    approval: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="SNAPSHOTTED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
