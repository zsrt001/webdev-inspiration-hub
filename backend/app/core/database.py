"""Async SQLAlchemy database configuration."""

import ssl
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

_SUPABASE_POOLER_REGIONS = (
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ca-central-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-east-1",
    "sa-east-1",
)
_SUPABASE_POOLER_PREFIXES = ("aws-0", "aws-1")
_detected_supabase_pooler_host: str | None = None


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


def _supabase_pooler_host() -> str:
    explicit = str(settings.supabase_pooler_host or "").strip()
    if explicit:
        return explicit
    region = str(settings.supabase_pooler_region or "us-east-1").strip() or "us-east-1"
    return f"aws-0-{region}.pooler.supabase.com"


def _is_supabase_direct_host(host: str) -> bool:
    return host.startswith("db.") and host.endswith(".supabase.co")


def _route_supabase_direct_to_pooler(raw: str) -> str:
    """Use Supabase's IPv4 pooler on Vercel when DATABASE_URL is the direct host."""
    if not settings.is_vercel_runtime:
        return raw

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if not _is_supabase_direct_host(host):
        return raw

    project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
    username = parts.username or "postgres"
    if project_ref and "." not in username:
        username = f"{username}.{project_ref}"

    userinfo = quote(username, safe="%")
    if parts.password is not None:
        userinfo = f"{userinfo}:{quote(parts.password, safe='%')}"

    netloc = f"{userinfo}@{_supabase_pooler_host()}:5432"
    return urlunsplit((parts.scheme, netloc, parts.path or "/postgres", parts.query, parts.fragment))


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _supabase_pooler_hosts() -> list[str]:
    explicit = str(settings.supabase_pooler_host or "").strip()
    if explicit:
        return [explicit]
    region = str(settings.supabase_pooler_region or "us-east-1").strip() or "us-east-1"
    preferred_hosts = [
        f"{prefix}-{region}.pooler.supabase.com"
        for prefix in _SUPABASE_POOLER_PREFIXES
    ]
    hosts = [
        f"{prefix}-{region}.pooler.supabase.com"
        for prefix in _SUPABASE_POOLER_PREFIXES
        for region in _SUPABASE_POOLER_REGIONS
    ]
    return [*preferred_hosts, *(host for host in hosts if host not in preferred_hosts)]


def _build_supabase_pooler_async_creator(database_url: str):
    """Build a Vercel-only connector that finds the correct Supabase pooler region."""
    if not settings.is_vercel_runtime:
        return None

    raw = str(database_url or "").strip()
    if raw.startswith("postgres://"):
        raw = f"postgresql+asyncpg://{raw[len('postgres://'):]}"
    elif raw.startswith("postgresql://"):
        raw = f"postgresql+asyncpg://{raw[len('postgresql://'):]}"
    raw = _quote_url_userinfo(raw)

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if not _is_supabase_direct_host(host):
        return None

    project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
    username = parts.username or "postgres"
    if project_ref and "." not in username:
        username = f"{username}.{project_ref}"
    password = parts.password or ""
    database = (parts.path or "/postgres").lstrip("/") or "postgres"
    ssl_arg = _ssl_context()

    async def creator():
        global _detected_supabase_pooler_host

        hosts = [_detected_supabase_pooler_host] if _detected_supabase_pooler_host else _supabase_pooler_hosts()
        errors: list[str] = []
        for pooler_host in [host for host in hosts if host]:
            try:
                connection = await asyncpg.connect(
                    user=username,
                    password=password,
                    database=database,
                    host=pooler_host,
                    port=5432,
                    ssl=ssl_arg,
                    statement_cache_size=0,
                    timeout=4.0,
                )
                _detected_supabase_pooler_host = pooler_host
                return connection
            except Exception as exc:
                errors.append(f"{pooler_host}: {type(exc).__name__}: {exc}")

        detail = " | ".join(errors[-3:]) if errors else "no pooler hosts configured"
        raise RuntimeError(f"supabase_pooler_detection_failed: {detail}")

    return creator


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
    raw = _route_supabase_direct_to_pooler(raw)
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
            if sslmode in {"require", "prefer", "verify-ca", "verify-full"}:
                connect_args["ssl"] = _ssl_context()
            elif sslmode == "disable" and (
                settings.runtime_environment != "development"
                or host not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("sslmode=disable is allowed only for local development PostgreSQL")
            continue
        kept_pairs.append((key, value))

    if is_supabase_host and "ssl" not in connect_args:
        connect_args["ssl"] = _ssl_context()

    if is_pooler_host:
        connect_args.setdefault("statement_cache_size", 0)

    normalized = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept_pairs), parts.fragment)
    )
    return normalized, connect_args


database_url, connect_args = normalize_database_url(settings.database_url)
supabase_pooler_async_creator = _build_supabase_pooler_async_creator(settings.database_url)

# Create async engine
engine_kwargs = {
    "echo": settings.debug,
    "future": True,
    "pool_pre_ping": True,
}
if supabase_pooler_async_creator is not None:
    engine_kwargs["async_creator"] = supabase_pooler_async_creator
else:
    engine_kwargs["connect_args"] = connect_args

engine = create_async_engine(database_url, **engine_kwargs)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

control_plane_raw_url = settings.effective_control_plane_database_url or settings.database_url
control_plane_database_url, control_plane_connect_args = normalize_database_url(control_plane_raw_url)
control_plane_async_creator = _build_supabase_pooler_async_creator(control_plane_raw_url)
control_plane_engine_kwargs = {
    "echo": settings.debug,
    "future": True,
    "pool_pre_ping": True,
}
if control_plane_async_creator is not None:
    control_plane_engine_kwargs["async_creator"] = control_plane_async_creator
else:
    control_plane_engine_kwargs["connect_args"] = control_plane_connect_args

control_plane_engine = create_async_engine(
    control_plane_database_url,
    **control_plane_engine_kwargs,
)
control_plane_async_session_maker = async_sessionmaker(
    control_plane_engine,
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


async def get_control_plane_db() -> AsyncSession:
    """Yield the dedicated audited control-plane writer session."""
    errors = settings.control_plane_database_config_errors
    if errors:
        raise RuntimeError("; ".join(errors))
    async with control_plane_async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
