"""Check the configured Supabase/Postgres connection."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from urllib.parse import urlsplit, urlunsplit


def _bootstrap_path() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


async def _run() -> int:
    from app.core.database import database_url, engine

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("select current_database(), current_user"))
            database_name, user_name = result.one()
        parsed = urlsplit(database_url)
        host_url = urlunsplit((parsed.scheme, parsed.hostname or "", "", "", ""))
        print(f"database_ok=true database={database_name} user={user_name} host={host_url}")
        return 0
    except Exception as exc:
        print(f"database_ok=false error={type(exc).__name__}: {exc}")
        return 2
    finally:
        await engine.dispose()


def main() -> int:
    _bootstrap_path()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
