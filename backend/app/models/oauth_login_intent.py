"""Browser-bound, single-use application login intent."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OAuthLoginIntent(Base):
    """Local anti-replay intent; Supabase remains responsible for OAuth state and PKCE."""

    __tablename__ = "oauth_login_intents"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_oauth_login_intents_token_hash"),
        CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_oauth_login_intents_token_hash_length",
        ),
        CheckConstraint(
            "char_length(browser_binding_hash) = 64",
            name="ck_oauth_login_intents_browser_hash_length",
        ),
        CheckConstraint(
            "left(redirect_path, 1) = '/' AND left(redirect_path, 2) <> '//' "
            "AND position(chr(92) in redirect_path) = 0 "
            "AND redirect_path !~ '[[:cntrl:]]'",
            name="ck_oauth_login_intents_local_redirect",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    browser_binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_path: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
