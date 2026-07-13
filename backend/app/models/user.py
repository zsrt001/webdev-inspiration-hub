"""User SQLAlchemy model."""

import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    """User model for web accounts."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject", name="uq_users_auth_provider_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    openid: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        comment="Stable local identity key",
    )
    username: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
        index=True,
        comment="Username for password-based sign-in",
    )
    password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Bcrypt password hash for password-based sign-in",
    )
    auth_provider: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
        comment="External auth provider, e.g. supabase/google",
    )
    auth_subject: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment="External provider subject/user id",
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    unionid: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
        comment="Legacy external identity value retained for migration inventory",
    )
    nickname: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(32),
        default="user",
        server_default="user",
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        server_default="active",
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    orders: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="user",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User {self.id} openid={self.openid[:8]}...>"
