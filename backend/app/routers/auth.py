"""Authentication API routes."""

import hashlib
import importlib
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import InMemoryRateLimiter
from app.core.supabase_auth import (
    SupabaseAuthError,
    SupabaseUserClaims,
    build_supabase_openid,
    verify_supabase_token,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, SupabaseSessionRequest

router = APIRouter()
settings = get_settings()

# JWT configuration
ALGORITHM = "HS256"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$")
NEW_ACCOUNT_IP_LIMITER = InMemoryRateLimiter(limit=settings.new_account_ip_limit_per_hour, window_seconds=3600)
NEW_ACCOUNT_DEVICE_LIMITER = InMemoryRateLimiter(limit=settings.new_account_device_limit_per_hour, window_seconds=3600)
DEFAULT_OAUTH_RETURN_PATH = "/pages/account/index"


def _load_bcrypt():
    try:
        return importlib.import_module("bcrypt")
    except ModuleNotFoundError as exc:
        if exc.name != "bcrypt._bcrypt":
            raise

        for module_name in list(sys.modules):
            if module_name == "bcrypt" or module_name.startswith("bcrypt."):
                sys.modules.pop(module_name, None)

        removed_paths: list[str] = []
        for path in list(sys.path):
            normalized = path.replace("\\", "/").lower()
            if "/app/_vendor" in normalized:
                sys.path.remove(path)
                removed_paths.append(path)

        try:
            return importlib.import_module("bcrypt")
        finally:
            for path in reversed(removed_paths):
                sys.path.insert(0, path)


bcrypt = _load_bcrypt()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Create a username/password account and immediately issue an app JWT."""
    username = _normalize_username(request.username)
    _validate_password(request.password)

    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already exists")

    _enforce_new_account_risk_limits(http_request)
    user = User(
        openid=_local_openid_for_username(username),
        username=username,
        password=_hash_password(request.password),
        auth_provider="password",
        auth_subject=username,
        nickname=username,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    await db.refresh(user)
    return _build_login_response(user)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """
    Login endpoint (code exchange).

    For current Web/MiniProgram flow, `code` is used as identity seed.
    A deterministic openid is derived from the provided code.
    If username/password are provided, this endpoint performs password login.
    """
    if request.username is not None or request.password is not None:
        return await _login_with_password(request, db)

    raw_code = (request.code or "").strip()
    if not raw_code:
        raise HTTPException(status_code=400, detail="Missing login code")

    identity_seed = raw_code[:1024]
    openid = f"wx_{hashlib.sha256(identity_seed.encode('utf-8')).hexdigest()[:32]}"

    # Check if user exists, create if not
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    if not user:
        _enforce_new_account_risk_limits(http_request)
        user = User(openid=openid)
        db.add(user)
        await db.flush()
        await db.refresh(user)
    user.last_login_at = datetime.now(timezone.utc)

    # Create JWT token
    access_token = create_access_token(
        data={"sub": str(user.id), "openid": openid}
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        openid=openid,
        user_id=user.id,
        username=user.username,
    )


@router.get("/supabase/google/start")
async def start_supabase_google_login(next: str | None = None) -> RedirectResponse:
    """Redirect the browser into Supabase's Google OAuth flow."""
    if not settings.supabase_oauth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Auth is not fully configured",
        )

    redirect_to = _oauth_return_url(next)
    query = urlencode(
        {
            "provider": "google",
            "redirect_to": redirect_to,
        }
    )
    authorize_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/authorize?{query}"
    return RedirectResponse(authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/supabase/session", response_model=LoginResponse)
async def exchange_supabase_session(
    request: SupabaseSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Exchange a Supabase OAuth access token for this app's JWT session."""
    try:
        claims = await verify_supabase_token(request.access_token)
    except SupabaseAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase session",
        ) from exc

    user = await _get_or_create_supabase_user(db, claims)
    await db.refresh(user)

    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "openid": user.openid,
            "auth_provider": "supabase",
            "email": user.email,
        }
    )
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        openid=user.openid,
        user_id=user.id,
        username=user.username,
    )


async def _login_with_password(request: LoginRequest, db: AsyncSession) -> LoginResponse:
    username = _normalize_username(request.username or "")
    password = request.password or ""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    stored_hash = user.password if user else None

    if not user or not stored_hash or not _verify_password(password, stored_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    return _build_login_response(user)


def _normalize_username(value: str) -> str:
    username = (value or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-64 characters and use letters, numbers, underscore, dot, or hyphen",
        )
    return username


def _validate_password(value: str) -> None:
    password = value or ""
    if len(password) < 6 or len(password) > 128:
        raise HTTPException(status_code=422, detail="Password must be 6-128 characters")


def _hash_password(value: str) -> str:
    return bcrypt.hashpw(_password_material(value), bcrypt.gensalt(rounds=12)).decode("ascii")


def _verify_password(value: str, password_hash: str) -> bool:
    if not value or not password_hash:
        return False
    try:
        return bcrypt.checkpw(_password_material(value), password_hash.encode("ascii"))
    except (TypeError, ValueError):
        return False


def _password_material(value: str) -> bytes:
    # Bcrypt only accepts 72 input bytes. Pre-hashing keeps long Unicode passwords stable.
    return hashlib.sha256(value.encode("utf-8")).hexdigest().encode("ascii")


def _local_openid_for_username(username: str) -> str:
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
    return f"local_{digest}"


def _build_login_response(user: User) -> LoginResponse:
    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "openid": user.openid,
            "auth_provider": user.auth_provider or "password",
            "username": user.username,
        }
    )
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        openid=user.openid,
        user_id=user.id,
        username=user.username,
    )


def _enforce_new_account_risk_limits(request: Request) -> None:
    client_ip = _client_ip(request)
    device_key = _device_key(request)
    if NEW_ACCOUNT_IP_LIMITER.is_limited(client_ip) or NEW_ACCOUNT_DEVICE_LIMITER.is_limited(device_key):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "new_account_rate_limited",
                "message": "Too many new accounts from this device or network. Please try again later.",
            },
        )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _device_key(request: Request) -> str:
    explicit = (
        request.headers.get("x-device-id")
        or request.headers.get("x-visitor-id")
        or request.headers.get("x-client-fingerprint")
        or ""
    ).strip()
    if explicit:
        return explicit[:128]
    user_agent = (request.headers.get("user-agent") or "unknown")[:256]
    return hashlib.sha256(f"{_client_ip(request)}:{user_agent}".encode("utf-8")).hexdigest()


def _oauth_return_url(next_path: str | None) -> str:
    path = (next_path or DEFAULT_OAUTH_RETURN_PATH).strip()
    if not path.startswith("/") or path.startswith("//"):
        path = DEFAULT_OAUTH_RETURN_PATH
    if path.startswith("/api/") or path.startswith("/auth/"):
        path = DEFAULT_OAUTH_RETURN_PATH

    base = settings.effective_frontend_base_url.rstrip("/")
    return f"{base}{path}"


async def _get_or_create_supabase_user(db: AsyncSession, claims: SupabaseUserClaims) -> User:
    result = await db.execute(
        select(User).where(
            User.auth_provider == "supabase",
            User.auth_subject == claims.subject,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        openid = build_supabase_openid(claims.subject)
        result = await db.execute(select(User).where(User.openid == openid))
        user = result.scalar_one_or_none()

    if user is None:
        _enforce_new_account_risk_limits_from_claims(claims)
        user = User(
            openid=build_supabase_openid(claims.subject),
            auth_provider="supabase",
            auth_subject=claims.subject,
        )
        db.add(user)

    user.auth_provider = "supabase"
    user.auth_subject = claims.subject
    user.email = claims.email
    if claims.nickname:
        user.nickname = claims.nickname[:64]
    if claims.avatar_url:
        user.avatar_url = claims.avatar_url[:512]
    if not user.role:
        user.role = "user"
    if not user.status:
        user.status = "active"
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    return user


def _enforce_new_account_risk_limits_from_claims(claims: SupabaseUserClaims) -> None:
    identity = claims.email or claims.subject
    key = f"supabase:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
    if NEW_ACCOUNT_DEVICE_LIMITER.is_limited(key):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "new_account_rate_limited",
                "message": "Too many new accounts from this identity. Please try again later.",
            },
        )
