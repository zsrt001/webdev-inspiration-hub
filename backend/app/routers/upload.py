"""Compatibility tombstones for the retired public-URL upload surface."""

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/upload", tags=["retired-upload"], include_in_schema=False)


def _raise_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "public_upload_retired",
            "message": "Use the authenticated private media upload endpoint.",
            "replacement": "/api/v1/media/uploads",
        },
    )


@router.post("")
async def retired_single_upload() -> None:
    """Reject before FastAPI/Starlette parses or spools a legacy file body."""

    _raise_retired()


@router.post("/multiple")
async def retired_multiple_upload() -> None:
    _raise_retired()


@router.post("/delete")
async def retired_url_delete() -> None:
    _raise_retired()
