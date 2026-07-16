"""Authenticated user profile and soft-closure API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.error_response import get_request_id
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.user import AccountCloseRequest, AccountCloseResponse, UserRead
from app.services.account_closure_service import AccountClosureError, close_account
from app.services.auth_session_service import clear_session_cookies

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def get_current_user_profile(
    current_user: User = Depends(get_session_user),
) -> User:
    """Get current authenticated user's business profile."""
    return current_user


@router.post("/me/close", response_model=AccountCloseResponse)
async def close_current_user_account(
    _payload: AccountCloseRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> AccountCloseResponse:
    """Soft-close the account without deleting financial or media facts."""

    try:
        tombstone = await close_account(
            db,
            user_id=current_user.id,
            closure_reason="USER_REQUESTED",
            audit_request_id=get_request_id(request),
        )
        await db.commit()
    except AccountClosureError as exc:
        await db.rollback()
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "account_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
    clear_session_cookies(response)
    return AccountCloseResponse(
        closed_at=tombstone.closed_at,
        media_cleanup_pending=tombstone.media_cleanup_pending,
    )
