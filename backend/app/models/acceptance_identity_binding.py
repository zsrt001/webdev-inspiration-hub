"""One-time deployment-bound acceptance identity authorization."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AcceptanceIdentityBinding(Base):
    __tablename__ = "acceptance_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "environment", "deployment_id", "provider", "subject_hmac",
            name="uq_acceptance_identity_binding_coordinate",
        ),
        CheckConstraint("environment IN ('preview', 'production')", name="ck_acceptance_binding_environment"),
        CheckConstraint(
            "revoked_at IS NULL OR (consumed_at IS NULL AND consumed_user_id IS NULL)",
            name="ck_acceptance_binding_revocation_unconsumed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    consumed_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
