"""Smart Input Gatekeeper API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict
import uuid

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.core.session_auth import get_session_user
from app.models.user import User
from app.services.feature_flag_service import require_request_capability
from app.services import gatekeeper_service

router = APIRouter(prefix="/gatekeeper", tags=["gatekeeper"])


class GatekeeperRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: uuid.UUID


class GatekeeperResponse(BaseModel):
    passed: bool
    reasons: List[str]
    advice: List[str]
    metrics: Dict[str, float]
    risk_flags: List[str] = []
    warnings: List[str] = []
    warning_advice: List[str] = []


@router.post("/check", response_model=GatekeeperResponse)
async def check_image_quality(
    payload: GatekeeperRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> GatekeeperResponse:
    await require_request_capability(
        request,
        db,
        Capability.AUTHENTICATED_UPLOAD,
        verified_user_id=current_user.id,
    )
    try:
        verdict = await gatekeeper_service.check_image_quality(
            db,
            owner_user_id=current_user.id,
            asset_id=payload.asset_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "image_analysis_failed", "message": "The image could not be analyzed."},
        ) from exc
    return GatekeeperResponse(**verdict.model_dump())
