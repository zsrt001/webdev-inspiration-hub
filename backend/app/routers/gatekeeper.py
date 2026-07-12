"""Smart Input Gatekeeper API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict

from app.core.database import get_db
from app.core.feature_flags import Capability
from app.services.feature_flag_service import require_request_capability
from app.services import gatekeeper_service

router = APIRouter(prefix="/gatekeeper", tags=["gatekeeper"])


class GatekeeperRequest(BaseModel):
    image_url: str


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
    request: GatekeeperRequest,
    db: AsyncSession = Depends(get_db),
) -> GatekeeperResponse:
    await require_request_capability(None, db, Capability.AUTHENTICATED_UPLOAD)
    try:
        verdict = await gatekeeper_service.check_image_quality(request.image_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to analyze image: {e}")
    return GatekeeperResponse(**verdict.model_dump())
