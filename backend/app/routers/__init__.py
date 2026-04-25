"""API routers."""

from fastapi import APIRouter

from app.routers import users, orders, auth, templates, upload, analytics, session, credits, payments, admin, gatekeeper, presets, leads, live_portrait, recommendations, ops

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(credits.router, prefix="/credits", tags=["credits"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(upload.router)
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(session.router, prefix="/session", tags=["session"])
api_router.include_router(gatekeeper.router)
api_router.include_router(presets.router)
api_router.include_router(leads.router)
api_router.include_router(live_portrait.router)
api_router.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
api_router.include_router(ops.router)
