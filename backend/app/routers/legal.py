"""Public legal policy routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.legal_policy_service import get_legal_policies

router = APIRouter()


@router.get("/policies")
async def legal_policies():
    """Return current public commercial, privacy, retention, and disclaimer policy copy."""
    return get_legal_policies()
