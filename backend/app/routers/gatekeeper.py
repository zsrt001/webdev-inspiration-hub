"""Smart Input Gatekeeper API routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict

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


@router.post("/check", response_model=GatekeeperResponse)
async def check_image_quality(request: GatekeeperRequest) -> GatekeeperResponse:
    try:
        verdict = await gatekeeper_service.check_image_quality(request.image_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to analyze image: {e}")
    return GatekeeperResponse(**verdict.model_dump())
