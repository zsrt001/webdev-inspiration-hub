"""Guest bootstrap login — code exchange."""

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse
from app.routers.auth._helpers import create_access_token, _enforce_new_account_risk_limits

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    raw_code = (request.code or "").strip()
    if not raw_code:
        raise HTTPException(status_code=400, detail="Missing login code")

    identity_seed = raw_code[:1024]
    openid = f"wx_{hashlib.sha256(identity_seed.encode('utf-8')).hexdigest()[:32]}"

    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    if not user:
        _enforce_new_account_risk_limits(http_request)
        user = User(openid=openid)
        db.add(user)
        await db.flush()
        await db.refresh(user)
    user.last_login_at = datetime.now(timezone.utc)

    access_token = create_access_token(data={"sub": str(user.id), "openid": openid})
    return LoginResponse(access_token=access_token, token_type="bearer", openid=openid, user_id=user.id, username=user.username)
