"""Runtime schema guards for production databases that lag migrations."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_user_account_columns_ready = False


async def ensure_user_account_columns(db: AsyncSession) -> None:
    """Ensure auth/admin user columns exist before ORM queries select the full User model."""
    global _user_account_columns_ready
    if _user_account_columns_ready:
        return

    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(32)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_subject VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
    ]
    for statement in statements:
        await db.execute(text(statement))

    for column in ("username", "auth_provider", "auth_subject", "email", "role", "status"):
        await db.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_users_{column}" ON users ("{column}")'))

    _user_account_columns_ready = True
