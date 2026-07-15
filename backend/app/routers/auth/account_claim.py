"""Customer-facing, payment-proof-bound empty legacy account claims."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.error_response import get_request_id
from app.core.session_auth import get_session_user
from app.models.user import User
from app.schemas.auth import (
    LegacyAccountMergeRequest,
    LegacyAccountMergeResponse,
    PaymentClaimProofRequest,
    PaymentClaimProofResponse,
)
from app.services.account_claim_proof_service import verify_payment_claim_reference
from app.services.account_merge_service import AccountClaimError, claim_legacy_account


router = APIRouter(prefix="/account-claims")


def _public_claim_error(error: AccountClaimError) -> HTTPException:
    if error.code == "ownership_proof_required":
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code},
        )
    if error.code in {
        "ownership_proof_expired",
        "ownership_proof_consumed",
        "ownership_proof_unavailable",
        "merge_graph_conflict",
        "commercial_lineage_not_ready",
    }:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        )
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "account_claim_unavailable"},
    )


@router.post("/payment-proof", response_model=PaymentClaimProofResponse)
async def create_payment_claim_proof(
    payload: PaymentClaimProofRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentClaimProofResponse:
    try:
        proof = await verify_payment_claim_reference(
            db,
            canonical_user_id=current_user.id,
            legacy_user_id=payload.legacy_user_id,
            payment_reference=payload.payment_reference,
            audit_request_id=get_request_id(request),
        )
        await db.commit()
    except AccountClaimError as exc:
        await db.rollback()
        raise _public_claim_error(exc) from exc
    return PaymentClaimProofResponse(proof_id=proof.id, expires_at=proof.expires_at)


@router.post("/merge", response_model=LegacyAccountMergeResponse)
async def merge_empty_legacy_account(
    payload: LegacyAccountMergeRequest,
    request: Request,
    current_user: User = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
) -> LegacyAccountMergeResponse:
    try:
        merge = await claim_legacy_account(
            db,
            canonical_user_id=current_user.id,
            legacy_user_id=payload.legacy_user_id,
            verified_proof_id=payload.proof_id,
            audit_request_id=get_request_id(request),
        )
        await db.commit()
    except AccountClaimError as exc:
        await db.rollback()
        raise _public_claim_error(exc) from exc
    return LegacyAccountMergeResponse(
        merge_id=merge.id,
        legacy_user_id=merge.legacy_user_id,
        canonical_user_id=merge.canonical_user_id,
    )
