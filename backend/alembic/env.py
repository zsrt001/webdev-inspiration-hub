"""Alembic environment for async SQLAlchemy migrations."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, normalize_database_url  # noqa: E402
import app.models  # noqa: F401,E402  Registers model metadata.


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
database_url, connect_args = normalize_database_url(settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in offline SQL rendering mode."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Configure Alembic and run migrations inside its transaction context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against an async database connection."""
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        do_run_migrations(existing_connection)
        return

    engine = create_async_engine(
        database_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
