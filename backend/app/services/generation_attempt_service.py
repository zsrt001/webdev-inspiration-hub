"""Fenced INITIAL attempt preparation and Evolink submission boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker
from app.core.feature_flags import Capability
from app.models.credit_reservation import CreditReservation
from app.models.asset_access_grant import AssetAccessGrant
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJobStatus
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.services.credit_reservation_service import (
    CreditInvariantViolation,
    capture_reservation,
    require_initial_submission_reservation,
)
from app.services.evolink_service import (
    EvolinkGenerationRequest,
    EvolinkProviderError,
    EvolinkService,
    EvolinkSubmitFact,
    evolink_service,
)
from app.services.feature_flag_service import require_worker_capability
from app.services.generation_job_service import validate_attempt_transition, validate_job_transition
from app.services.generation_policy import build_generation_negative_prompt, build_studio_generation_prompt
from app.services.job_lease_service import (
    JobLease,
    pause_generation_reconciliation,
    require_current_generation_fence,
)
from app.services.media_asset_service import create_provider_grant
from app.services.partner_invite_service import (
    PartnerInviteError,
    require_partner_generation_allowed,
)
from app.services.qa_rules import build_structured_qa_issues
from app.services.repair_policy import should_include_previous_edit_result
from app.services.template_service import get_template_by_id


class GenerationAttemptBoundaryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    attempt_id: uuid.UUID
    job_id: uuid.UUID
    reservation_id: uuid.UUID
    request: EvolinkGenerationRequest
    attempt_kind: GenerationAttemptKind = GenerationAttemptKind.INITIAL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _locked_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> GenerationAttempt:
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if attempt is None:
        raise GenerationAttemptBoundaryError("generation_attempt_not_found")
    return attempt


async def require_partner_submission_allowed(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> None:
    """Serialize Partner withdrawal against the durable submit boundary."""

    try:
        await require_partner_generation_allowed(db, job_id=job_id)
    except PartnerInviteError as exc:
        raise GenerationAttemptBoundaryError(exc.code) from exc


async def prepare_initial_generation_attempt(
    db: AsyncSession,
    *,
    lease: JobLease,
) -> GenerationAttempt:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    existing = await db.scalar(
        select(GenerationAttempt)
        .where(
            GenerationAttempt.job_id == job.id,
            GenerationAttempt.kind == GenerationAttemptKind.INITIAL,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            str(existing.client_request_id) != str(job.submission_correlation_id)
            or existing.provider != "evolink"
        ):
            raise GenerationAttemptBoundaryError("initial_attempt_identity_mismatch")
        return existing
    attempt = GenerationAttempt.prepared(
        job=job,
        attempt_number=1,
        kind=GenerationAttemptKind.INITIAL,
        provider="evolink",
    )
    db.add(attempt)
    await db.flush()
    job.active_attempt_id = attempt.id
    return attempt


def _source_asset_ids(order: Order) -> tuple[uuid.UUID, ...]:
    values = order.source_asset_ids
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise GenerationAttemptBoundaryError("generation_source_assets_invalid")
    try:
        parsed = tuple(uuid.UUID(str(value)) for value in values)
    except (TypeError, ValueError, AttributeError) as exc:
        raise GenerationAttemptBoundaryError("generation_source_assets_invalid") from exc
    if len(set(parsed)) != len(parsed):
        raise GenerationAttemptBoundaryError("generation_source_assets_invalid")
    return parsed


def _build_request(order: Order, grant_urls: tuple[str, ...]) -> EvolinkGenerationRequest:
    snapshot = order.product_policy_snapshot
    if not isinstance(snapshot, dict):
        raise GenerationAttemptBoundaryError("generation_policy_snapshot_missing")
    template_id = str(snapshot.get("template_id") or order.template_id or "").strip()
    template = get_template_by_id(template_id)
    if template is None:
        raise GenerationAttemptBoundaryError("generation_template_missing")
    try:
        subject_count = int(snapshot["subject_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationAttemptBoundaryError("generation_subject_count_invalid") from exc
    if subject_count != len(grant_urls) or subject_count not in {1, 2}:
        raise GenerationAttemptBoundaryError("generation_subject_count_mismatch")
    prompt = build_studio_generation_prompt(
        template=template,
        prompt_override=snapshot.get("prompt_override"),
        global_style_text=snapshot.get("global_style_text"),
        scene_text=snapshot.get("scene_text"),
        outfit_text=snapshot.get("outfit_text"),
        is_couple=subject_count == 2,
    )
    negative = build_generation_negative_prompt(
        is_couple=subject_count == 2,
        template=template,
    )
    compact = EvolinkService.compact_prompt(
        f"{prompt}\nNegative: {negative}",
        subject_count=subject_count,
    )
    from app.core.config import get_settings

    runtime = get_settings()
    return EvolinkGenerationRequest(
        model=runtime.evolink_image_model,
        prompt=compact,
        image_urls=grant_urls,
        size=runtime.evolink_image_size,
        quality=runtime.evolink_image_quality,
        model_params={"web_search": False},
    )


def _build_repair_request(
    order: Order,
    *,
    identity_grant_urls: tuple[str, ...],
    previous_candidate_url: str | None,
    reasons: tuple[str, ...],
    repair_number: int,
) -> EvolinkGenerationRequest:
    if not 1 <= int(repair_number) <= 2 or not 1 <= len(reasons) <= 16:
        raise GenerationAttemptBoundaryError("generation_repair_directive_invalid")
    base = _build_request(order, identity_grant_urls)
    issues = build_structured_qa_issues(list(reasons), source="immutable_verdict")
    directives = "; ".join(
        f"{issue['code']}: {issue['repair_hint']}"
        for issue in issues
        if issue.get("repair_hint")
    )
    if not directives:
        raise GenerationAttemptBoundaryError("generation_repair_directive_invalid")
    prompt = EvolinkService.compact_prompt(
        (
            f"TARGETED REPAIR {int(repair_number)}. Fix only these verified defects: "
            f"{directives}. Preserve identity, subject count, wardrobe, scene, pose, and all "
            f"already-correct regions. {base.prompt}"
        ),
        subject_count=len(identity_grant_urls),
    )
    candidate_urls = (
        (str(previous_candidate_url),)
        if str(previous_candidate_url or "").strip()
        else ()
    )
    return EvolinkGenerationRequest(
        model=base.model,
        prompt=prompt,
        image_urls=(*identity_grant_urls, *candidate_urls),
        size=base.size,
        quality=base.quality,
        model_params=dict(base.model_params),
    )


async def prepare_submission_boundary(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> PreparedSubmission:
    """Commit-ready SUBMITTING state; this function performs no Provider I/O."""
    current = now or _utcnow()
    await require_partner_submission_allowed(db, job_id=lease.job_id)
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=current,
    )
    attempt = await _locked_attempt(db, attempt_id)
    if attempt.job_id != job.id:
        raise GenerationAttemptBoundaryError("generation_attempt_job_mismatch")
    if GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.PREPARED:
        raise GenerationAttemptBoundaryError("generation_attempt_not_prepared")
    attempt_kind = GenerationAttemptKind(attempt.kind)
    if attempt_kind not in {
        GenerationAttemptKind.INITIAL,
        GenerationAttemptKind.REPAIR,
    }:
        raise GenerationAttemptBoundaryError("generation_attempt_kind_not_submittable")

    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if order is None or order.user_id != user_id or order.generation_job_id != job.id:
        raise GenerationAttemptBoundaryError("generation_order_job_mismatch")
    if order.reservation_id is None:
        raise GenerationAttemptBoundaryError("generation_reservation_missing")
    repair_snapshot: dict | None = None
    repair_reasons: tuple[str, ...] = ()
    repair_candidate_id: uuid.UUID | None = None
    if attempt_kind is GenerationAttemptKind.INITIAL:
        await require_initial_submission_reservation(
            db,
            reservation_id=order.reservation_id,
            order_id=order.id,
            user_id=user_id,
            now=current,
        )
    else:
        from app.services.generation_repair_service import _load_repair_capture_lineage

        locked_order, _reservation, _initial = await _load_repair_capture_lineage(db, job)
        if locked_order.id != order.id or locked_order.user_id != user_id:
            raise GenerationAttemptBoundaryError("generation_repair_order_lineage_mismatch")
        repair_snapshot = attempt.request_snapshot
        if (
            not isinstance(repair_snapshot, dict)
            or repair_snapshot.get("schema") != "generation-repair.v1"
            or str(repair_snapshot.get("source_verdict_id"))
            != str(attempt.source_verdict_id)
        ):
            raise GenerationAttemptBoundaryError("generation_repair_snapshot_invalid")
        raw_reasons = repair_snapshot.get("reason_codes")
        if not isinstance(raw_reasons, list) or not 1 <= len(raw_reasons) <= 16:
            raise GenerationAttemptBoundaryError("generation_repair_snapshot_invalid")
        repair_reasons = tuple(str(reason) for reason in raw_reasons)
        try:
            repair_candidate_id = uuid.UUID(str(repair_snapshot["candidate_asset_id"]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise GenerationAttemptBoundaryError("generation_repair_snapshot_invalid") from exc
    await require_worker_capability(
        db,
        Capability.GENERATION,
        deployment_id=job.api_deployment_id,
        runtime_bundle_id=job.runtime_bundle_id,
        worker_image_digest=job.expected_worker_image_digest,
        user_id=user_id,
    )

    asset_ids = _source_asset_ids(order)
    all_asset_ids = (
        (*asset_ids, repair_candidate_id)
        if repair_candidate_id is not None
        else asset_ids
    )
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(all_asset_ids))
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {asset.id: asset for asset in assets}
    if set(by_id) != set(all_asset_ids):
        raise GenerationAttemptBoundaryError("generation_input_asset_missing")
    identity_assets = [by_id[asset_id] for asset_id in asset_ids]
    for asset in identity_assets:
        if (
            asset.owner_user_id != user_id
            or MediaAssetRole(asset.role) is not MediaAssetRole.SOURCE
            or MediaAssetStatus(asset.status) is not MediaAssetStatus.ACTIVE
            or asset.read_revoked_at is not None
            or asset.expires_at <= current
        ):
            raise GenerationAttemptBoundaryError("generation_source_asset_unavailable")
        if asset.order_id not in {None, order.id} or asset.job_id not in {None, job.id}:
            raise GenerationAttemptBoundaryError("generation_source_asset_lineage_conflict")
        asset.order_id = order.id
        asset.job_id = job.id

    candidate_asset = (
        by_id[repair_candidate_id]
        if repair_candidate_id is not None
        else None
    )
    if candidate_asset is not None and (
        candidate_asset.owner_user_id != user_id
        or MediaAssetRole(candidate_asset.role) is not MediaAssetRole.CANDIDATE
        or MediaAssetStatus(candidate_asset.status) is not MediaAssetStatus.ACTIVE
        or candidate_asset.read_revoked_at is not None
        or candidate_asset.expires_at <= current
        or candidate_asset.order_id != order.id
        or candidate_asset.job_id != job.id
    ):
        raise GenerationAttemptBoundaryError("generation_repair_candidate_unavailable")
    include_candidate = bool(
        candidate_asset is not None
        and should_include_previous_edit_result(list(repair_reasons))
    )
    ordered_assets = [
        *identity_assets,
        *([candidate_asset] if include_candidate and candidate_asset is not None else []),
    ]

    issued = [
        await create_provider_grant(
            db,
            asset=asset,
            provider="evolink",
            purpose="generation-input",
            job_id=job.id,
            attempt_id=attempt.id,
            commit=False,
            now=current,
        )
        for asset in ordered_assets
    ]
    identity_urls = tuple(item.read_url for item in issued[: len(identity_assets)])
    if attempt_kind is GenerationAttemptKind.INITIAL:
        request = _build_request(order, identity_urls)
    else:
        request = _build_repair_request(
            order,
            identity_grant_urls=identity_urls,
            previous_candidate_url=(issued[-1].read_url if include_candidate else None),
            reasons=repair_reasons,
            repair_number=int(attempt.attempt_number) - 1,
        )
    provider_snapshot = {
        "schema": "evolink-generation-request.v1",
        "model": request.model,
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        "input_asset_ids": [str(asset.id) for asset in ordered_assets],
        "grant_ids": [str(item.grant.id) for item in issued],
        "image_count": len(request.image_urls),
        "size": request.size,
        "quality": request.quality,
    }
    attempt.request_snapshot = (
        provider_snapshot
        if repair_snapshot is None
        else {**repair_snapshot, "provider_request": provider_snapshot}
    )
    attempt.status = validate_attempt_transition(
        attempt.status,
        GenerationAttemptStatus.SUBMITTING,
    )
    attempt.submit_started_at = current
    order.status = (
        OrderStatus.GENERATING
        if attempt_kind is GenerationAttemptKind.INITIAL
        else OrderStatus.REPAIRING
    )
    return PreparedSubmission(
        attempt_id=attempt.id,
        job_id=job.id,
        reservation_id=order.reservation_id,
        request=request,
        attempt_kind=GenerationAttemptKind(attempt.kind),
    )


async def mark_submission_unknown(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    reason: str,
    now: datetime | None = None,
) -> GenerationAttempt:
    current = now or _utcnow()
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=current,
    )
    attempt = await _locked_attempt(db, attempt_id)
    if attempt.job_id != job.id or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.SUBMITTING:
        raise GenerationAttemptBoundaryError("generation_attempt_unknown_transition_invalid")
    attempt.status = validate_attempt_transition(attempt.status, GenerationAttemptStatus.UNKNOWN)
    job.status = validate_job_transition(job.status, GenerationJobStatus.RECONCILING)
    job.next_retry_at = current if attempt.provider_job_id else None
    job.last_error_code = str(reason)[:64]
    job.lease_owner = None
    job.lease_claim_id = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    order = await db.scalar(select(Order).where(Order.id == job.order_id).with_for_update())
    if order is not None:
        order.status = OrderStatus.UNKNOWN_EXTERNAL_STATE
    return attempt


async def record_preaccept_rejection(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    provider_code: str,
    retryable: bool,
    terminal: bool,
    now: datetime | None = None,
) -> GenerationAttempt | int:
    """Record a Provider-confirmed non-acceptance before any safe retry."""

    current = now or _utcnow()
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=current,
    )
    attempt = await _locked_attempt(db, attempt_id)
    if (
        attempt.job_id != job.id
        or job.active_attempt_id != attempt.id
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.SUBMITTING
    ):
        raise GenerationAttemptBoundaryError("generation_preaccept_rejection_state_invalid")
    code = str(provider_code or "").strip()[:64]
    if not code.startswith("evolink_submit_rejected_"):
        raise GenerationAttemptBoundaryError("generation_preaccept_rejection_code_invalid")
    snapshot = dict(attempt.request_snapshot or {})
    raw_history = snapshot.get("preaccept_rejections", [])
    if not isinstance(raw_history, list) or len(raw_history) > 2:
        raise GenerationAttemptBoundaryError("generation_preaccept_rejection_history_invalid")
    history = [dict(item) for item in raw_history if isinstance(item, dict)]
    if len(history) != len(raw_history):
        raise GenerationAttemptBoundaryError("generation_preaccept_rejection_history_invalid")
    history.append(
        {
            "sequence": len(history) + 1,
            "code": code,
            "retryable": bool(retryable),
            "terminal": bool(terminal),
        }
    )
    snapshot["preaccept_rejections"] = history
    attempt.request_snapshot = snapshot
    job.retry_count = int(job.retry_count or 0) + 1
    job.last_error_code = code
    if not terminal:
        return len(history)

    attempt.status = validate_attempt_transition(
        attempt.status,
        GenerationAttemptStatus.FAILED,
    )
    attempt.finished_at = current
    grants = list(
        (
            await db.scalars(
                select(AssetAccessGrant)
                .where(AssetAccessGrant.attempt_id == attempt.id)
                .with_for_update()
            )
        ).all()
    )
    for grant in grants:
        if grant.revoked_at is None:
            grant.revoked_at = current
    from app.services.generation_repair_service import fail_generation_and_settle

    reason = (
        "provider_preaccept_retry_exhausted"
        if retryable
        else "provider_preaccept_rejected"
    )
    await fail_generation_and_settle(
        db,
        job_id=job.id,
        reason_code=reason,
        reason_detail=code,
        allow_release_reserved=(
            GenerationAttemptKind(attempt.kind) is GenerationAttemptKind.INITIAL
        ),
        worker_id=lease.worker_id,
        lease_claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=current,
    )
    return attempt


async def persist_submitted_fact(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    fact: EvolinkSubmitFact,
) -> GenerationAttempt:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    attempt = await _locked_attempt(db, attempt_id)
    if attempt.job_id != job.id:
        raise GenerationAttemptBoundaryError("generation_attempt_job_mismatch")
    status = GenerationAttemptStatus(attempt.status)
    if status is GenerationAttemptStatus.SUBMITTED:
        if attempt.provider_job_id != fact.task_id:
            raise GenerationAttemptBoundaryError("generation_provider_task_conflict")
        return attempt
    if status is not GenerationAttemptStatus.SUBMITTING:
        raise GenerationAttemptBoundaryError("generation_attempt_not_submitting")
    attempt.status = validate_attempt_transition(status, GenerationAttemptStatus.SUBMITTED)
    attempt.provider_job_id = fact.task_id
    attempt.cost_minor_units = fact.cost_minor_units
    attempt.cost_currency = fact.currency
    attempt.submitted_at = _utcnow()
    job.retry_count = 0
    job.last_error_code = None
    if GenerationAttemptKind(attempt.kind) is GenerationAttemptKind.INITIAL:
        attempt.submission_accounting_state = "PENDING"
    elif attempt.submission_accounting_state != "NOT_CAPTURED":
        raise GenerationAttemptBoundaryError("generation_repair_capture_state_invalid")
    return attempt


async def capture_initial_submission(
    db: AsyncSession,
    *,
    prepared: PreparedSubmission,
    lease: JobLease,
) -> GenerationAttempt:
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    attempt = await _locked_attempt(db, prepared.attempt_id)
    if (
        attempt.job_id != job.id
        or GenerationAttemptKind(attempt.kind) is not GenerationAttemptKind.INITIAL
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.SUBMITTED
    ):
        raise GenerationAttemptBoundaryError("generation_initial_capture_attempt_invalid")
    await capture_reservation(
        db,
        reservation_id=prepared.reservation_id,
        provider_attempt_id=attempt.id,
        idempotency_key=f"capture:{attempt.id}",
    )
    attempt.submission_accounting_state = "CAPTURED"
    return attempt


async def require_repair_submission_capture(
    db: AsyncSession,
    *,
    prepared: PreparedSubmission,
    lease: JobLease,
) -> GenerationAttempt:
    """Revalidate the original captured authorization without debiting a repair."""

    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    attempt = await _locked_attempt(db, prepared.attempt_id)
    if (
        attempt.job_id != job.id
        or job.active_attempt_id != attempt.id
        or GenerationAttemptKind(attempt.kind) is not GenerationAttemptKind.REPAIR
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.SUBMITTED
        or attempt.submission_accounting_state != "NOT_CAPTURED"
    ):
        raise GenerationAttemptBoundaryError("generation_repair_accounting_invalid")
    from app.services.generation_repair_service import _load_repair_capture_lineage

    await _load_repair_capture_lineage(db, job)
    return attempt


async def ensure_accepted_submission_accounting(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    user_id: uuid.UUID,
) -> GenerationAttempt:
    """Finish the durable capture gap before consuming an accepted task result."""

    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
    )
    attempt = await _locked_attempt(db, attempt_id)
    if (
        attempt.job_id != job.id
        or job.active_attempt_id != attempt.id
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.SUBMITTED
    ):
        raise GenerationAttemptBoundaryError("generation_accounting_attempt_invalid")
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if (
        order is None
        or order.user_id != user_id
        or order.generation_job_id != job.id
        or order.reservation_id is None
    ):
        raise GenerationAttemptBoundaryError("generation_accounting_order_invalid")
    kind = GenerationAttemptKind(attempt.kind)
    if kind is GenerationAttemptKind.REPAIR:
        if attempt.submission_accounting_state != "NOT_CAPTURED":
            raise GenerationAttemptBoundaryError("generation_repair_capture_state_invalid")
        from app.services.generation_repair_service import _load_repair_capture_lineage

        await _load_repair_capture_lineage(db, job)
        return attempt
    if kind is not GenerationAttemptKind.INITIAL:
        raise GenerationAttemptBoundaryError("generation_accounting_attempt_kind_invalid")
    if attempt.submission_accounting_state == "CAPTURED":
        return attempt
    if attempt.submission_accounting_state != "PENDING":
        raise GenerationAttemptBoundaryError("generation_initial_capture_state_invalid")
    await capture_reservation(
        db,
        reservation_id=order.reservation_id,
        provider_attempt_id=attempt.id,
        idempotency_key=f"capture:{attempt.id}",
    )
    attempt.submission_accounting_state = "CAPTURED"
    return attempt


async def submit_generation_attempt(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    user_id: uuid.UUID,
    provider: EvolinkService = evolink_service,
) -> GenerationAttempt:
    """Persist SUBMITTING before HTTP and never replay an ambiguous POST."""
    prepared = await prepare_submission_boundary(
        db,
        attempt_id=attempt_id,
        lease=lease,
        user_id=user_id,
    )
    await db.commit()
    retries_used = 0
    while True:
        try:
            fact = await provider.submit(prepared.request, attempt_id=prepared.attempt_id)
        except (httpx.TimeoutException, httpx.WriteError):
            attempt = await mark_submission_unknown(
                db,
                attempt_id=attempt_id,
                lease=lease,
                reason="submit_response_lost",
            )
            await db.commit()
            return attempt
        except EvolinkProviderError as exc:
            if exc.acceptance_possible:
                attempt = await mark_submission_unknown(
                    db,
                    attempt_id=attempt_id,
                    lease=lease,
                    reason="submit_acceptance_ambiguous",
                )
                await db.commit()
                return attempt
            from app.core.config import get_settings

            terminal = (
                not exc.retryable
                or retries_used >= int(get_settings().generation_max_retries)
            )
            recorded = await record_preaccept_rejection(
                db,
                attempt_id=attempt_id,
                lease=lease,
                provider_code=exc.code,
                retryable=exc.retryable,
                terminal=terminal,
            )
            await db.commit()
            if terminal:
                if not isinstance(recorded, GenerationAttempt):
                    return recorded
                return recorded
            retries_used += 1
            await asyncio.sleep(min(4, 2 ** (retries_used - 1)))
            await require_current_generation_fence(
                db,
                job_id=lease.job_id,
                worker_id=lease.worker_id,
                claim_id=lease.claim_id,
                fencing_token=lease.fencing_token,
            )
            continue
        break
    attempt = await persist_submitted_fact(
        db,
        attempt_id=attempt_id,
        lease=lease,
        fact=fact,
    )
    await db.commit()
    if prepared.attempt_kind is GenerationAttemptKind.INITIAL:
        attempt = await capture_initial_submission(db, prepared=prepared, lease=lease)
    else:
        attempt = await require_repair_submission_capture(
            db,
            prepared=prepared,
            lease=lease,
        )
    await db.commit()
    return attempt


async def execute_claimed_generation_job(*, lease: JobLease, user_id: uuid.UUID) -> None:
    """Worker-facing entry; unverified provider correlation stops before HTTP."""
    async with async_session_maker() as db:
        attempt = await prepare_initial_generation_attempt(db, lease=lease)
        await db.commit()
        status = GenerationAttemptStatus(attempt.status)
        if status is GenerationAttemptStatus.PREPARED:
            attempt = await submit_generation_attempt(
                db,
                attempt_id=attempt.id,
                lease=lease,
                user_id=user_id,
            )
            if GenerationAttemptStatus(attempt.status) is GenerationAttemptStatus.SUBMITTED:
                await pause_generation_reconciliation(
                    db,
                    lease=lease,
                    reason="provider_task_pending",
                    retry_after_seconds=5,
                )
                await db.commit()
        elif status in {
            GenerationAttemptStatus.SUBMITTING,
            GenerationAttemptStatus.UNKNOWN,
        }:
            return
        elif status is GenerationAttemptStatus.SUBMITTED:
            return
        else:
            raise GenerationAttemptBoundaryError("generation_attempt_not_executable")


async def execute_claimed_generation_attempt(
    *,
    lease: JobLease,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> None:
    """Execute only an already-persisted active REPAIR attempt."""

    async with async_session_maker() as db:
        job = await require_current_generation_fence(
            db,
            job_id=lease.job_id,
            worker_id=lease.worker_id,
            claim_id=lease.claim_id,
            fencing_token=lease.fencing_token,
        )
        attempt = await _locked_attempt(db, attempt_id)
        if (
            attempt.job_id != job.id
            or job.active_attempt_id != attempt.id
            or GenerationAttemptKind(attempt.kind) is not GenerationAttemptKind.REPAIR
        ):
            raise GenerationAttemptBoundaryError("generation_repair_attempt_not_active")
        status = GenerationAttemptStatus(attempt.status)
        if status is GenerationAttemptStatus.PREPARED:
            attempt = await submit_generation_attempt(
                db,
                attempt_id=attempt.id,
                lease=lease,
                user_id=user_id,
            )
            if GenerationAttemptStatus(attempt.status) is GenerationAttemptStatus.SUBMITTED:
                await pause_generation_reconciliation(
                    db,
                    lease=lease,
                    reason="provider_task_pending",
                    retry_after_seconds=5,
                )
                await db.commit()
            return
        if status in {
            GenerationAttemptStatus.SUBMITTING,
            GenerationAttemptStatus.SUBMITTED,
            GenerationAttemptStatus.UNKNOWN,
        }:
            return
        raise GenerationAttemptBoundaryError("generation_repair_attempt_not_executable")
