"""Authenticated account export endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.account_export import AccountExport
from app.services.account_export_service import AccountExportError, build_account_export


router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExport)
async def export_current_account(
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download only the authenticated user's canonical and merge-linked facts."""

    try:
        export = await build_account_export(db, current_user.id)
    except AccountExportError as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "account_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
    return Response(
        content=export.model_dump_json(),
        media_type="application/json",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Content-Disposition": f'attachment; filename="vowpic-account-{export.export_id}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
