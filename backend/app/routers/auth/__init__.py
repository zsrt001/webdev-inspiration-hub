"""Authentication API routes."""

from fastapi import APIRouter

from app.routers.auth.account_claim import router as account_claim_router
from app.routers.auth.google import router as google_router
from app.routers.auth.session import router as session_router

router = APIRouter()
router.include_router(account_claim_router)
router.include_router(google_router)
router.include_router(session_router)
