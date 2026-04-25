"""Live Portrait API routes (M3 add-on)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.user_auth import get_request_user
from app.core.task_queue import enqueue_live_portrait
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.models.user import User
from app.services.generation_service import generation_service
from app.services.credit_service import (
    add_credits_async,
    deduct_credits_async,
    get_balance_async,
    get_live_portrait_cost,
)
from app.services import gatekeeper_service

settings = get_settings()

router = APIRouter(prefix="/live_portrait", tags=["live_portrait"])


class LivePortraitRequest(BaseModel):
    image_url: str = Field(min_length=8, max_length=1024)
    seconds: int = Field(default=5, ge=3, le=10)


class LivePortraitResponse(BaseModel):
    success: bool
    job_id: str
    status: str | None = None
    video_url: str | None = None
    source_image_url: str | None = None
    seconds: int | None = None
    message: str | None = None
    created_at: datetime | None = None
    credits_cost: int | None = None
    refunded_credits: int | None = None
    failure_code: str | None = None
    reused: bool = False
    retryable: bool | None = None
    failure_action: str | None = None


def _response_meta(job: LivePortraitJob | None) -> tuple[int | None, int | None, str | None]:
    if not job:
        return None, None, None
    params = job.generation_params if isinstance(job.generation_params, dict) else {}
    credits_cost = int(params.get("credits_cost") or job.credits_cost or 0)
    refunded_credits = params.get("refunded_credits")
    refunded_credits = int(refunded_credits or 0) if refunded_credits is not None else None
    failure_code = str(params.get("failure_code") or "").strip() or None
    return credits_cost, refunded_credits, failure_code


def _retryable_for_failure(failure_code: str | None) -> bool | None:
    if not failure_code:
        return None
    return failure_code in {"node_error", "delivery_error", "video_output_empty", "unknown_error"}


def _failure_action_for_code(failure_code: str | None) -> str | None:
    if not failure_code:
        return None
    if failure_code == "workflow_error":
        return "contact_support"
    if failure_code == "model_missing":
        return "contact_support"
    if failure_code in {"node_error", "delivery_error"}:
        return "retry_later"
    if failure_code == "video_output_empty":
        return "retry_with_other_image"
    return "retry_later"


def _job_response(
    job: LivePortraitJob,
    *,
    message: str | None = None,
    reused: bool = False,
    credits_cost_override: int | None = None,
) -> LivePortraitResponse:
    credits_cost, refunded_credits, failure_code = _response_meta(job)
    return LivePortraitResponse(
        success=True,
        job_id=str(job.id),
        status=job.status.value if isinstance(job.status, LivePortraitStatus) else str(job.status),
        video_url=job.video_url,
        source_image_url=job.source_image_url,
        seconds=int(job.seconds or 5),
        message=message if message is not None else job.error_message,
        created_at=job.created_at,
        credits_cost=credits_cost_override if credits_cost_override is not None else credits_cost,
        refunded_credits=refunded_credits,
        failure_code=failure_code,
        reused=reused,
        retryable=_retryable_for_failure(failure_code),
        failure_action=_failure_action_for_code(failure_code),
    )


@router.post("/generate", response_model=LivePortraitResponse)
async def generate_live_portrait(
    request: LivePortraitRequest,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
) -> LivePortraitResponse:
    """
    Create a Live Portrait job and enqueue it to the ARQ worker.

    Pricing:
    - Charges credits when job is successfully enqueued.
    - On queue failure: auto-refund.
    - On generation failure in worker: auto-refund.
    """
    if not settings.live_portrait_enabled:
        return LivePortraitResponse(
            success=False,
            job_id="",
            status="DISABLED",
            video_url=None,
            message="Live Portrait is disabled.",
        )
    if not generation_service.supports_live_portrait():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_provider_unsupported",
                "message": "Live Portrait is not supported by the current generation provider.",
            },
        )

    try:
        gatekeeper = await gatekeeper_service.check_image_quality(request.image_url)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "gatekeeper_fetch_failed",
                "message": str(e),
            },
        )
    if not gatekeeper.passed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "gatekeeper_reject",
                "reasons": gatekeeper.reasons,
                "advice": gatekeeper.advice,
                "metrics": gatekeeper.metrics,
                "risk_flags": gatekeeper.risk_flags,
            },
        )

    try:
        generation_service.validate_runtime_requirements()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_runtime_invalid",
                "message": f"Generation runtime invalid: {e}",
            },
        )

    active_result = await db.execute(
        select(LivePortraitJob)
        .where(
            LivePortraitJob.user_id == current_user.id,
            LivePortraitJob.source_image_url == request.image_url,
            LivePortraitJob.seconds == int(request.seconds),
            LivePortraitJob.status.in_([LivePortraitStatus.CREATED, LivePortraitStatus.GENERATING]),
        )
        .order_by(LivePortraitJob.created_at.desc())
        .limit(1)
    )
    active_job = active_result.scalar_one_or_none()
    if active_job:
        return _job_response(active_job, message="Already queued", reused=True, credits_cost_override=0)

    reuse_since = datetime.utcnow() - timedelta(hours=24)
    completed_result = await db.execute(
        select(LivePortraitJob)
        .where(
            LivePortraitJob.user_id == current_user.id,
            LivePortraitJob.source_image_url == request.image_url,
            LivePortraitJob.seconds == int(request.seconds),
            LivePortraitJob.status == LivePortraitStatus.COMPLETED,
            LivePortraitJob.video_url.is_not(None),
            LivePortraitJob.updated_at >= reuse_since,
        )
        .order_by(LivePortraitJob.updated_at.desc())
        .limit(1)
    )
    completed_job = completed_result.scalar_one_or_none()
    if completed_job:
        return _job_response(completed_job, message="Reusing recent result", reused=True, credits_cost_override=0)

    credits_cost = get_live_portrait_cost(seconds=request.seconds)
    if not await deduct_credits_async(db, current_user.id, credits_cost):
        balance = await get_balance_async(db, current_user.id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": "Not enough credits. Please top up to continue.",
                "required": credits_cost,
                "balance": balance,
            },
        )

    job = LivePortraitJob(
        user_id=current_user.id,
        status=LivePortraitStatus.CREATED,
        source_image_url=request.image_url,
        seconds=int(request.seconds),
        credits_cost=int(credits_cost),
        generation_params={
            "credits_cost": int(credits_cost),
            "gatekeeper": gatekeeper.model_dump(),
        },
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(job)

    try:
        queue_job_id = await enqueue_live_portrait(str(job.id))
    except Exception as e:
        await add_credits_async(db, current_user.id, int(credits_cost))
        job.status = LivePortraitStatus.FAILED
        job.error_message = f"queue_unavailable: {e}"
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "queue_unavailable",
                "message": "Live Portrait queue unavailable. Please try again later.",
            },
        )

    base_params = job.generation_params if isinstance(job.generation_params, dict) else {}
    base_params["queue_job_id"] = queue_job_id
    job.generation_params = base_params
    job.task_id = queue_job_id
    job.status = LivePortraitStatus.GENERATING
    await db.commit()

    return LivePortraitResponse(
        success=True,
        job_id=str(job.id),
        status=job.status.value,
        video_url=None,
        source_image_url=job.source_image_url,
        seconds=int(job.seconds or 5),
        message="Queued",
        created_at=job.created_at,
        credits_cost=int(credits_cost),
        refunded_credits=0,
        failure_code=None,
        reused=False,
        retryable=None,
        failure_action=None,
    )


@router.get("/list", response_model=list[LivePortraitResponse])
async def list_live_portrait_jobs(
    limit: int = Query(default=6, ge=1, le=20),
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
) -> list[LivePortraitResponse]:
    result = await db.execute(
        select(LivePortraitJob)
        .where(LivePortraitJob.user_id == current_user.id)
        .order_by(LivePortraitJob.created_at.desc())
        .limit(int(limit))
    )
    jobs = result.scalars().all()
    return [_job_response(job) for job in jobs]


@router.get("/{job_id}", response_model=LivePortraitResponse)
async def get_live_portrait_job(
    job_id: str,
    current_user: User = Depends(get_request_user),
    db: AsyncSession = Depends(get_db),
) -> LivePortraitResponse:
    """Poll a Live Portrait job."""
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    result = await db.execute(select(LivePortraitJob).where(LivePortraitJob.id == job_uuid))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_response(job)
