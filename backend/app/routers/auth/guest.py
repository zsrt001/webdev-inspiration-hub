"""Permanent tombstone for the retired guest/OpenID bootstrap login."""

from fastapi import APIRouter, Depends, HTTPException


def _raise_guest_auth_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "guest_auth_retired",
            "message": "Guest/OpenID bootstrap login is permanently retired.",
        },
    )


router = APIRouter(dependencies=[Depends(_raise_guest_auth_retired)])


@router.post("/login")
async def login() -> None:
    _raise_guest_auth_retired()
