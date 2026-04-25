"""Authentication API routes."""

import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter()
settings = get_settings()

# JWT configuration
ALGORITHM = "HS256"


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


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """
    Login endpoint (code exchange).

    For current Web/MiniProgram flow, `code` is used as identity seed.
    A deterministic openid is derived from the provided code.
    """
    raw_code = (request.code or "").strip()
    if not raw_code:
        raise HTTPException(status_code=400, detail="Missing login code")

    identity_seed = raw_code[:1024]
    openid = f"wx_{hashlib.sha256(identity_seed.encode('utf-8')).hexdigest()[:32]}"

    # Check if user exists, create if not
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    if not user:
        user = User(openid=openid)
        db.add(user)
        await db.flush()
        await db.refresh(user)

    # Create JWT token
    access_token = create_access_token(
        data={"sub": str(user.id), "openid": openid}
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        openid=openid,
        user_id=user.id,
    )
