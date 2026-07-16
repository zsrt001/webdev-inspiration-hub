"""API routers."""

from fastapi import APIRouter

from app.routers import admin, analytics, auth, credits, gatekeeper, legal, media, ops, ops_admin, orders, partner_invites, payments, presets, retired, runtime, subscriptions, templates, upload, users

api_router = APIRouter()

api_router.include_router(runtime.router)
api_router.include_router(retired.router)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(partner_invites.router)
api_router.include_router(credits.router, prefix="/credits", tags=["credits"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(upload.router)
api_router.include_router(media.router)
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(gatekeeper.router)
api_router.include_router(presets.router)
api_router.include_router(ops.router)
api_router.include_router(ops_admin.router)
api_router.include_router(legal.router, prefix="/legal", tags=["legal"])
