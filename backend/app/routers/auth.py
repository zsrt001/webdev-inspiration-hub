"""Authentication API routes."""

import hashlib
import importlib
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy import or_, select, update
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
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    SendVerificationRequest,
    SupabaseSessionRequest,
)
from app.services.credit_service import grant_welcome_bonus
from app.services.account_risk_service import check_new_account_risk_limits, record_account_risk_event
from app.services.email_service import (
    is_disposable_email,
    is_ip_verification_rate_limited,
    is_verification_rate_limited,
    record_ip_verification,
    send_verification_code,
    verify_email_code,
)
from app.services.schema_guard_service import ensure_user_account_columns

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# JWT configuration
ALGORITHM = "HS256"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{2,63}$")
NEW_ACCOUNT_IP_LIMITER = InMemoryRateLimiter(limit=settings.new_account_ip_limit_per_hour, window_seconds=3600)
NEW_ACCOUNT_DEVICE_LIMITER = InMemoryRateLimiter(limit=settings.new_account_device_limit_per_hour, window_seconds=3600)
LOGIN_IP_LIMITER = InMemoryRateLimiter(limit=20, window_seconds=900)  # 20 attempts per 15min per IP
LOGIN_USER_LIMITER = InMemoryRateLimiter(limit=10, window_seconds=900)  # 10 attempts per 15min per username
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


# ---------------------------------------------------------------------------
# Email verification endpoints
# ---------------------------------------------------------------------------

@router.post("/send-verification", status_code=status.HTTP_200_OK)
async def send_verification(
    request: SendVerificationRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a 6-digit verification code to the given email."""
    email = request.email.strip().lower()

    # Block disposable email providers
    if is_disposable_email(email):
        await record_account_risk_event(
            db,
            event_type="email_verification_blocked_disposable",
            request=http_request,
            email=email,
            provider="password",
            risk_score=80,
        )
        raise HTTPException(
            status_code=422,
            detail="Disposable email addresses are not allowed. Please use a permanent email.",
        )

    # Per-email rate limit
    if is_verification_rate_limited(email):
        await record_account_risk_event(
            db,
            event_type="email_verification_blocked_email_rate",
            request=http_request,
            email=email,
            provider="password",
            risk_score=60,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many verification requests. Please wait before trying again.",
        )

    # Per-IP rate limit (prevents mass farming from one IP)
    client_ip = _client_ip(http_request)
    if is_ip_verification_rate_limited(client_ip):
        await record_account_risk_event(
            db,
            event_type="email_verification_blocked_ip_rate",
            request=http_request,
            email=email,
            provider="password",
            risk_score=70,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many verification requests from this network. Please try again later.",
        )

    result = await send_verification_code(
        email,
        db=db,
        metadata=_welcome_bonus_metadata(http_request, provider="password"),
    )
    if not result.get("sent"):
        await record_account_risk_event(
            db,
            event_type="email_verification_send_failed",
            request=http_request,
            email=email,
            provider="password",
            risk_score=10,
            metadata={"reason": result.get("reason") or result.get("status") or result.get("error")},
        )
        raise HTTPException(
            status_code=503,
            detail="Failed to send verification email. Please try again later.",
        )

    record_ip_verification(client_ip)
    await record_account_risk_event(
        db,
        event_type="email_verification_sent",
        request=http_request,
        email=email,
        provider="password",
        metadata={"provider_message_id": result.get("id")},
    )
    return {"message": "Verification code sent", "email": email}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Create a username/password account with verified email."""
    await ensure_user_account_columns(db)
    username = _normalize_username(request.username)
    _validate_password(request.password)
    email = request.email.strip().lower()

    # Block disposable email providers
    if is_disposable_email(email):
        await record_account_risk_event(
            db,
            event_type="password_register_blocked_disposable",
            request=http_request,
            email=email,
            provider="password",
            risk_score=80,
        )
        raise HTTPException(status_code=422, detail="Disposable email addresses are not allowed")

    # Verify the email code
    if not verify_email_code(email, request.verification_code):
        await record_account_risk_event(
            db,
            event_type="password_register_blocked_bad_code",
            request=http_request,
            email=email,
            provider="password",
            risk_score=30,
        )
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already exists")

    result = await db.execute(select(User).where(User.email == email))
    existing_email_user = result.scalar_one_or_none()
    if existing_email_user and existing_email_user.password:
        await record_account_risk_event(
            db,
            event_type="password_register_blocked_duplicate_email",
            request=http_request,
            user=existing_email_user,
            email=email,
            provider="password",
            risk_score=20,
        )
        raise HTTPException(status_code=409, detail="Email already registered")

    is_new_user = existing_email_user is None
    if is_new_user:
        await _enforce_new_account_risk_limits_persistent(
            db,
            http_request,
            email=email,
            provider="password",
        )
        user = User(
            openid=_local_openid_for_username(username),
            username=username,
            password=_hash_password(request.password),
            email=email,
            email_verified_at=datetime.now(timezone.utc),
            auth_provider="password",
            auth_subject=username,
            nickname=username,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(user)
    else:
        user = existing_email_user
        _ensure_user_active(user)
        if user.username:
            raise HTTPException(status_code=409, detail="Account already has a username")
        user.username = username
        user.password = _hash_password(request.password)
        user.email = email
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
        user.nickname = user.nickname or username
        user.last_login_at = datetime.now(timezone.utc)
        if not user.auth_provider:
            user.auth_provider = "password"
            user.auth_subject = username

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        detail = "Username already exists"
        if "email" in str(exc).lower():
            detail = "Email already registered"
        raise HTTPException(status_code=409, detail=detail) from exc
    await db.refresh(user)

    bonus_granted = await grant_welcome_bonus(db, user.id, metadata=_welcome_bonus_metadata(http_request, provider="password"))
    await record_account_risk_event(
        db,
        event_type="password_register_created" if is_new_user else "password_register_linked",
        request=http_request,
        user=user,
        email=email,
        provider="password",
        metadata={"welcome_bonus_granted": bonus_granted},
    )

    # Merge guest account if provided
    if request.previous_guest_id:
        await _merge_guest_account(db, request.previous_guest_id, user.id)

    return _build_login_response(user)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

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
    await ensure_user_account_columns(db)
    if request.username is not None or request.password is not None:
        response = await _login_with_password(request, db, http_request)
        if request.previous_guest_id:
            user_id = response.user_id
            await _merge_guest_account(db, request.previous_guest_id, user_id)
        return response

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


# ---------------------------------------------------------------------------
# Supabase / Google OAuth
# ---------------------------------------------------------------------------

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
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Exchange a Supabase OAuth access token for this app's JWT session."""
    await ensure_user_account_columns(db)
    try:
        claims = await verify_supabase_token(request.access_token)
    except SupabaseAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase session",
        ) from exc

    is_new_user, user = await _get_or_create_supabase_user(db, claims, http_request)
    await db.refresh(user)

    bonus_granted = False
    if is_new_user:
        bonus_granted = await grant_welcome_bonus(db, user.id, metadata=_welcome_bonus_metadata(http_request, provider="google"))
    await record_account_risk_event(
        db,
        event_type="google_register_created" if is_new_user else "google_login_linked",
        request=http_request,
        user=user,
        email=user.email,
        provider="google",
        metadata={"welcome_bonus_granted": bonus_granted},
    )

    # Merge guest account if provided
    if request.previous_guest_id:
        await _merge_guest_account(db, request.previous_guest_id, user.id)

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _login_with_password(request: LoginRequest, db: AsyncSession, http_request: Request | None = None) -> LoginResponse:
    login_id = (request.username or "").strip().lower()
    if not login_id:
        raise HTTPException(status_code=422, detail="Username or email is required")
    username = _normalize_username(login_id) if "@" not in login_id else login_id
    password = request.password or ""

    # Rate limit login attempts (only failed attempts count)
    if http_request:
        client_ip = _client_ip(http_request)
        if LOGIN_IP_LIMITER.check_only(client_ip) or LOGIN_USER_LIMITER.check_only(username):
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Please wait a few minutes.",
            )

    if "@" in login_id:
        result = await db.execute(select(User).where(User.email == login_id))
    else:
        result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    stored_hash = user.password if user else None

    if not user or not stored_hash or not _verify_password(password, stored_hash):
        if http_request:
            LOGIN_IP_LIMITER.record(_client_ip(http_request))
            LOGIN_USER_LIMITER.record(username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    _ensure_user_active(user)
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
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(status_code=422, detail="Password must be 8-128 characters")


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


def _ensure_user_active(user: User) -> None:
    status_value = (user.status or "active").strip().lower()
    if status_value not in {"active", ""}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "account_not_active",
                "message": "This account is not active. Please contact support.",
                "status": status_value,
            },
        )


def _welcome_bonus_metadata(request: Request | None, *, provider: str) -> dict:
    if request is None:
        return {"provider": provider}
    return {
        "provider": provider,
        "ip_hash": hashlib.sha256(_client_ip(request).encode("utf-8")).hexdigest()[:16],
        "device_hash": hashlib.sha256(_device_key(request).encode("utf-8")).hexdigest()[:16],
        "policy": "starter_single_generation_only",
    }


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


async def _enforce_new_account_risk_limits_persistent(
    db: AsyncSession,
    request: Request,
    *,
    email: str | None,
    provider: str,
) -> None:
    try:
        _enforce_new_account_risk_limits(request)
    except HTTPException:
        await record_account_risk_event(
            db,
            event_type="new_account_blocked_memory_rate",
            request=request,
            email=email,
            provider=provider,
            risk_score=75,
        )
        raise

    limit_hit = await check_new_account_risk_limits(
        db,
        request=request,
        ip_limit=settings.new_account_ip_limit_per_hour,
        device_limit=settings.new_account_device_limit_per_hour,
    )
    if limit_hit:
        await record_account_risk_event(
            db,
            event_type=f"new_account_blocked_{limit_hit['scope']}_rate",
            request=request,
            email=email,
            provider=provider,
            risk_score=75,
            metadata=limit_hit,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "new_account_rate_limited",
                "message": "Too many new accounts from this device or network. Please try again later.",
                **limit_hit,
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


async def _get_or_create_supabase_user(
    db: AsyncSession,
    claims: SupabaseUserClaims,
    request: Request,
) -> tuple[bool, User]:
    """Returns (is_new_user, user)."""
    is_new = False
    normalized_email = (claims.email or "").strip().lower() or None
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

    if user is None and normalized_email:
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()

    if user is None:
        if normalized_email and is_disposable_email(normalized_email):
            raise HTTPException(status_code=422, detail="Disposable email addresses are not allowed")
        await _enforce_new_account_risk_limits_persistent(
            db,
            request,
            email=normalized_email,
            provider="google",
        )
        _enforce_new_account_risk_limits_from_claims(claims)
        user = User(
            openid=build_supabase_openid(claims.subject),
            auth_provider="supabase",
            auth_subject=claims.subject,
        )
        db.add(user)
        is_new = True

    _ensure_user_active(user)
    user.auth_provider = "supabase"
    user.auth_subject = claims.subject
    user.email = normalized_email
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
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
    return is_new, user


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


# ---------------------------------------------------------------------------
# Guest account merge
# ---------------------------------------------------------------------------

async def _merge_guest_account(db: AsyncSession, guest_id: str, new_user_id: uuid.UUID) -> None:
    """Transfer orders and live portrait jobs from a guest user to the registered user."""
    guest_id = (guest_id or "").strip()
    if not guest_id:
        return

    # Try to find the guest user by possible openid formats
    possible_openids = []

    # Format from X-Visitor-Id header: visitor_guest_<uuid>
    if guest_id.startswith("guest_"):
        possible_openids.append(f"visitor_{guest_id}")

    # Format from /auth/login code exchange: wx_<sha256("web_<guest_id>")>
    code_seed = f"web_{guest_id}"
    wx_openid = f"wx_{hashlib.sha256(code_seed.encode('utf-8')).hexdigest()[:32]}"
    possible_openids.append(wx_openid)

    if not possible_openids:
        return

    # Find the guest user
    guest_user: User | None = None
    for openid in possible_openids:
        result = await db.execute(select(User).where(User.openid == openid))
        guest_user = result.scalar_one_or_none()
        if guest_user:
            break

    if not guest_user or guest_user.id == new_user_id:
        return

    # Only merge if the target is truly a guest (no password, no verified email)
    if guest_user.password or guest_user.email_verified_at:
        logger.warning("Refused merge: guest_id %s points to a non-guest account %s", guest_id, guest_user.id)
        return

    guest_user_id = guest_user.id

    # Transfer orders
    from app.models.order import Order
    await db.execute(
        update(Order).where(Order.user_id == guest_user_id).values(user_id=new_user_id)
    )

    # Transfer live portrait jobs
    from app.models.live_portrait_job import LivePortraitJob
    await db.execute(
        update(LivePortraitJob).where(LivePortraitJob.user_id == guest_user_id).values(user_id=new_user_id)
    )

    await db.flush()
    logger.info("Merged guest account %s -> %s (orders + live_portrait_jobs)", guest_user_id, new_user_id)
