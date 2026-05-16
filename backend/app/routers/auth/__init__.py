"""Authentication API routes — guest bootstrap, Google OAuth, and account merge."""

from fastapi import APIRouter

from app.routers.auth.guest import router as guest_router
from app.routers.auth.google import router as google_router

router = APIRouter()
router.include_router(guest_router)
router.include_router(google_router)
