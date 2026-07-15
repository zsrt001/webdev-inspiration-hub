"""Local browser session lifecycle endpoints."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.error_response import error_response
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.user import UserRead
from app.services.auth_session_service import (
    SessionServiceError,
    clear_session_cookies,
    logout_session,
    rotate_session,
)


router = APIRouter()

@router.get("/me", response_model=UserRead)
async def get_current_session_user(user: User = Depends(get_session_user)) -> User:
    return user


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh_browser_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await rotate_session(db, request, response)
    except SessionServiceError as exc:
        failure = error_response(
            request=request,
            status_code=exc.status_code,
            detail={"code": exc.code},
        )
        if exc.clear_session:
            clear_session_cookies(failure)
        return failure
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_browser_session(
    request: Request,
    response: Response,
    _: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await logout_session(db, request, response)
    except SessionServiceError as exc:
        failure = error_response(
            request=request,
            status_code=exc.status_code,
            detail={"code": exc.code},
        )
        if exc.clear_session:
            clear_session_cookies(failure)
        return failure
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
