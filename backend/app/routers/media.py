"""Authenticated private-media uploads, metadata, grants, and Admin probe."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_user
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.services.external_fetch_service import ExternalFetchError, fetch_admin_https
from app.services.feature_flag_service import require_request_capability
from app.services.media_asset_service import (
    AssetAccessError,
    UploadBatchError,
    UploadValidationError,
    load_owner_source_asset,
    request_owner_asset_deletion,
    stream_authenticated_multipart_upload,
    stream_provider_grant,
    validate_provider_grant_origin,
)
from app.services.upload_quota_service import UploadQuotaExceeded


router = APIRouter(prefix="/media", tags=["media"])


class MediaAssetResponse(BaseModel):
    asset_id: uuid.UUID
    width: int
    height: int
    mime_type: str
    byte_size: int
    expires_at: datetime


class UploadBatchResponse(BaseModel):
    batch_id: uuid.UUID
    assets: list[MediaAssetResponse]


class AdminFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class AdminFetchResponse(BaseModel):
    width: int
    height: int
    mime_type: str
    byte_size: int
    sha256: str
    evidence_eligible: bool = False


class MediaDeletionResponse(BaseModel):
    asset_id: uuid.UUID
    code: str
    blockers: list[str]


@router.post("/uploads", response_model=UploadBatchResponse)
async def upload_media(
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> UploadBatchResponse:
    """Stream a multipart body only after session, Origin, CSRF, flag, and quota admission."""

    await require_request_capability(
        request,
        db,
        Capability.AUTHENTICATED_UPLOAD,
        verified_user_id=current_user.id,
    )
    try:
        result = await stream_authenticated_multipart_upload(request, current_user, db)
    except UploadQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": exc.code, "message": "Upload quota is temporarily exhausted.", "retryable": True},
        ) from exc
    except UploadValidationError as exc:
        field_errors = []
        if exc.field:
            field_errors.append({"field": exc.field, "message": str(exc)})
        raise HTTPException(
            status_code=422,
            detail={
                "code": "upload_batch_rejected",
                "message": "One or more images were rejected.",
                "retryable": False,
                "field_errors": field_errors,
            },
        ) from exc
    except UploadBatchError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "The private upload could not be completed.", "retryable": True},
        ) from exc
    return UploadBatchResponse.model_validate(result)


@router.get("/grants/{token}", include_in_schema=False)
async def read_provider_grant(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip()
        if "," in forwarded_proto:
            raise AssetAccessError("asset_grant_origin_invalid")
        validate_provider_grant_origin(
            host_header=str(request.headers.get("host") or ""),
            request_scheme=forwarded_proto or request.url.scheme,
        )
        result = await stream_provider_grant(db, token=token)
    except AssetAccessError as exc:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found"}) from exc
    return StreamingResponse(
        BytesIO(result.content),
        media_type=result.mime_type,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.post("/admin/fetch", response_model=AdminFetchResponse)
async def admin_fetch_external_image(
    payload: AdminFetchRequest,
    _admin_user: User = Depends(require_admin_user),
) -> AdminFetchResponse:
    try:
        image = await fetch_admin_https(payload.url)
    except ExternalFetchError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": "The external image could not be fetched safely."},
        ) from exc
    return AdminFetchResponse(
        width=image.width,
        height=image.height,
        mime_type=image.mime_type,
        byte_size=image.byte_size,
        sha256=image.sha256,
        evidence_eligible=False,
    )


@router.get("/{asset_id}")
async def read_owner_source_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        result = await load_owner_source_asset(
            db,
            user=current_user,
            asset_id=asset_id,
        )
    except AssetAccessError as exc:
        status_code = 403 if exc.code in {"asset_forbidden", "asset_role_forbidden"} else 404
        raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
    return StreamingResponse(
        BytesIO(result.content),
        media_type=result.mime_type,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.delete("/{asset_id}", response_model=MediaDeletionResponse)
async def delete_owner_asset(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> MediaDeletionResponse:
    try:
        result = await request_owner_asset_deletion(
            db,
            user=current_user,
            asset_id=asset_id,
        )
    except AssetAccessError as exc:
        status_code = 403 if exc.code == "asset_forbidden" else 404
        raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
    return MediaDeletionResponse(
        asset_id=result.asset.id,
        code=result.code,
        blockers=list(result.blockers),
    )
