"""Permanent tombstone for the retired guest/OpenID bootstrap login."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.auth import LoginRequest, LoginResponse


def _raise_guest_auth_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "guest_auth_retired",
            "message": "Guest/OpenID bootstrap login is permanently retired.",
        },
    )


router = APIRouter(dependencies=[Depends(_raise_guest_auth_retired)])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
) -> LoginResponse:
    _ = request, http_request
    _raise_guest_auth_retired()
