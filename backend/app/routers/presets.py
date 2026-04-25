"""Presets API routes (Director Mode)."""

from fastapi import APIRouter

from app.services.preset_service import list_director_presets

router = APIRouter(prefix="/presets", tags=["presets"])


@router.get("/director")
async def get_director_presets():
    """Return curated outfit + scene presets for Director Mode."""
    return list_director_presets()

