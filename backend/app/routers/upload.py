"""File upload router."""

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_auth import require_admin_token
from app.core.database import get_db
from app.core.feature_flags import Capability
from app.services.feature_flag_service import require_request_capability
from app.services.storage import storage_service

router = APIRouter(prefix="/upload", tags=["upload"])


class UploadResponse(BaseModel):
    """Response for file upload."""
    url: str
    filename: str


class DeleteRequest(BaseModel):
    """Request to delete a previously uploaded file (best-effort)."""

    url: str


class DeleteResponse(BaseModel):
    """Response for file deletion."""

    success: bool


@router.post("", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a file to the configured storage provider.
    
    Returns:
        url: Public URL of the uploaded file
        filename: Original filename
    """
    await require_request_capability(None, db, Capability.AUTHENTICATED_UPLOAD)

    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}",
        )
    
    # Validate file size (max 10MB)
    max_size = 10 * 1024 * 1024  # 10MB
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 10MB.",
        )
    
    # Reset file position
    await file.seek(0)
    
    try:
        # Upload to storage provider
        url = storage_service.upload_file(
            file_content=file.file,
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "image/jpeg",
            folder="user-uploads",
        )
        
        return UploadResponse(url=url, filename=file.filename or "upload.jpg")
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}",
        )


@router.post("/multiple", response_model=list[UploadResponse])
async def upload_multiple_files(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload multiple files to S3 storage.
    
    Returns:
        List of upload responses with URLs
    """
    await require_request_capability(None, db, Capability.AUTHENTICATED_UPLOAD)
    if len(files) > 5:
        raise HTTPException(
            status_code=400,
            detail="Maximum 5 files allowed per upload.",
        )
    
    results = []
    for file in files:
        # Validate each file
        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        if file.content_type not in allowed_types:
            continue
        
        try:
            url = storage_service.upload_file(
                file_content=file.file,
                filename=file.filename or "upload.jpg",
                content_type=file.content_type or "image/jpeg",
                folder="user-uploads",
            )
            results.append(UploadResponse(url=url, filename=file.filename or "upload.jpg"))
        except Exception:
            continue
    
    return results


@router.post("/delete", response_model=DeleteResponse)
async def delete_file(
    request: DeleteRequest,
    _: None = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    """
    Delete an uploaded file from the configured storage provider.

    Note: This is best-effort in MVP; production should restrict deletion by auth + ownership.
    """
    await require_request_capability(None, db, Capability.AUTHENTICATED_UPLOAD)
    ok = storage_service.delete_file(request.url)
    return DeleteResponse(success=bool(ok))
