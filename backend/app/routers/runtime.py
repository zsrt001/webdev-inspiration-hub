"""Public, read-only runtime attestation route."""

from fastapi import APIRouter

from app.services.runtime_bundle_service import public_runtime_bundle_json


router = APIRouter(tags=["runtime"])


@router.get("/version")
async def runtime_version() -> dict[str, str]:
    return public_runtime_bundle_json()
