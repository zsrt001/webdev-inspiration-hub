"""Session API routes for cross-device Remote Join."""

from fastapi import APIRouter, HTTPException, File, UploadFile
from pydantic import BaseModel
from typing import Optional

from app.services.session_service import session_service, SessionStatus

from app.core.config import get_settings

settings = get_settings()

router = APIRouter()


def _session_store_error(exc: RuntimeError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


def _ensure_remote_join_enabled() -> None:
    if settings.remote_join_enabled:
        return
    raise HTTPException(
        status_code=503,
        detail={
            "error": "remote_join_disabled",
            "message": "Remote join is disabled in the current commercial deployment.",
        },
    )


class CreateSessionRequest(BaseModel):
    """Request to create a new session."""
    template_id: str
    host_image_url: Optional[str] = None


class CreateSessionResponse(BaseModel):
    """Response with session details and QR code."""
    session_id: str
    join_url: str
    qr_code_url: str
    expires_in_minutes: int


class SessionStatusResponse(BaseModel):
    """Response for session status polling."""
    exists: bool
    status: str
    host_ready: bool = False
    guest_ready: bool = False
    order_id: Optional[str] = None
    template_id: Optional[str] = None


class UploadResponse(BaseModel):
    """Response for image upload."""
    success: bool
    message: str
    session_status: str


class SessionImagesResponse(BaseModel):
    """Response with both uploaded images."""
    host_image_url: str
    guest_image_url: str
    template_id: str


class BindOrderRequest(BaseModel):
    """Bind an order_id to this session for guest viewing."""

    order_id: str


class SessionShareMetaResponse(BaseModel):
    """Share card metadata for inviting a partner to join."""

    session_id: str
    join_url: str
    mp_path: str
    title: str
    description: str
    image_url: str | None = None


@router.post("/create", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
    """
    Create a new session for couple photo upload.
    Returns a QR code URL that the partner can scan to join.
    """
    _ensure_remote_join_enabled()
    try:
        result = await session_service.create_session(
            template_id=request.template_id,
            host_image_url=request.host_image_url
        )
    except RuntimeError as e:
        raise _session_store_error(e)
    return CreateSessionResponse(**result)


@router.get("/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(session_id: str) -> SessionStatusResponse:
    """
    Get current session status.
    PC frontend polls this to know when guest has uploaded.
    """
    _ensure_remote_join_enabled()
    try:
        result = await session_service.get_status(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    return SessionStatusResponse(**result)


@router.post("/{session_id}/upload/host", response_model=UploadResponse)
async def upload_host_image(session_id: str, image_url: str) -> UploadResponse:
    """
    Upload host (PC user) image to session.
    """
    _ensure_remote_join_enabled()
    try:
        success = await session_service.upload_host_image(session_id, image_url)
    except RuntimeError as e:
        raise _session_store_error(e)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    try:
        status = await session_service.get_status(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    return UploadResponse(
        success=True,
        message="Host image uploaded",
        session_status=status["status"]
    )


@router.post("/{session_id}/upload/guest", response_model=UploadResponse)
async def upload_guest_image(session_id: str, image_url: str) -> UploadResponse:
    """
    Upload guest (mobile user) image to session.
    Called when partner scans QR and uploads their selfie.
    """
    _ensure_remote_join_enabled()
    try:
        success = await session_service.upload_guest_image(session_id, image_url)
    except RuntimeError as e:
        raise _session_store_error(e)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    
    try:
        status = await session_service.get_status(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    return UploadResponse(
        success=True,
        message="Guest image uploaded successfully! Look at the big screen.",
        session_status=status["status"]
    )


@router.get("/{session_id}/images", response_model=SessionImagesResponse)
async def get_session_images(session_id: str) -> SessionImagesResponse:
    """
    Get both images once session is ready.
    Used to start AI generation.
    """
    _ensure_remote_join_enabled()
    try:
        images = await session_service.get_images(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    if not images:
        raise HTTPException(
            status_code=400, 
            detail="Session not ready or not found"
        )
    
    return SessionImagesResponse(**images)


@router.get("/{session_id}/share_meta", response_model=SessionShareMetaResponse)
async def get_session_share_meta(session_id: str) -> SessionShareMetaResponse:
    """Get share card metadata for inviting a partner."""
    _ensure_remote_join_enabled()
    try:
        meta = await session_service.get_share_meta(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return SessionShareMetaResponse(**meta)


@router.post("/{session_id}/processing")
async def mark_processing(session_id: str):
    """Mark session as processing (AI generation started)."""
    _ensure_remote_join_enabled()
    try:
        await session_service.mark_processing(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    return {"success": True}


@router.post("/{session_id}/complete")
async def mark_complete(session_id: str):
    """Mark session as completed."""
    _ensure_remote_join_enabled()
    try:
        await session_service.mark_completed(session_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    return {"success": True}


@router.post("/{session_id}/bind_order")
async def bind_order(session_id: str, request: BindOrderRequest):
    """Bind order_id so the guest can jump to the same result."""
    _ensure_remote_join_enabled()
    try:
        ok = await session_service.bind_order(session_id, request.order_id)
    except RuntimeError as e:
        raise _session_store_error(e)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"success": True, "order_id": request.order_id}
