"""Async SQLAlchemy database configuration."""

import ssl
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()


def _quote_url_userinfo(raw: str) -> str:
    """Percent-encode user/password characters that break URL parsing."""
    if "://" not in raw:
        return raw

    scheme, remainder = raw.split("://", 1)
    authority_end_candidates = [
        idx for idx in (remainder.find("/"), remainder.find("?"), remainder.find("#")) if idx >= 0
    ]
    authority_end = min(authority_end_candidates) if authority_end_candidates else len(remainder)
    authority = remainder[:authority_end]
    suffix = remainder[authority_end:]
    if "@" not in authority:
        return raw

    userinfo, hostport = authority.rsplit("@", 1)
    if ":" in userinfo:
        username, password = userinfo.split(":", 1)
        safe_userinfo = f"{quote(username, safe='%')}:{quote(password, safe='%')}"
    else:
        safe_userinfo = quote(userinfo, safe="%")
    return f"{scheme}://{safe_userinfo}@{hostport}{suffix}"


def normalize_database_url(database_url: str) -> tuple[str, dict]:
    """Normalize common hosted Postgres URLs for SQLAlchemy's asyncpg driver."""
    raw = str(database_url or "").strip()
    if raw.startswith("postgres://"):
        raw = f"postgresql+asyncpg://{raw[len('postgres://'):]}"
    elif raw.startswith("postgresql://"):
        raw = f"postgresql+asyncpg://{raw[len('postgresql://'):]}"

    if not raw:
        return raw, {}

    raw = _quote_url_userinfo(raw)
    parts = urlsplit(raw)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    connect_args: dict = {}
    kept_pairs: list[tuple[str, str]] = []
    host = (parts.hostname or "").lower()
    is_supabase_host = host.endswith(".supabase.co") or "pooler.supabase." in host
    is_pooler_host = "pooler.supabase." in host

    for key, value in query_pairs:
        lowered = key.lower()
        if lowered == "sslmode":
            sslmode = value.strip().lower()
            if sslmode in {"require", "prefer"}:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                connect_args["ssl"] = ssl_context
            elif sslmode in {"verify-ca", "verify-full"}:
                connect_args["ssl"] = ssl.create_default_context()
            continue
        kept_pairs.append((key, value))

    if is_supabase_host and "ssl" not in connect_args:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context

    if is_pooler_host:
        connect_args.setdefault("statement_cache_size", 0)

    normalized = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept_pairs), parts.fragment)
    )
    return normalized, connect_args


database_url, connect_args = normalize_database_url(settings.database_url)

# Create async engine
engine = create_async_engine(
    database_url,
    echo=settings.debug,
    future=True,
    pool_pre_ping=True,
    connect_args=connect_args,
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base class."""

    pass


async def get_db() -> AsyncSession:
    """Dependency to get async database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
