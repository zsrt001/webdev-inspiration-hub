"""Fenced strict-QA execution and immutable per-candidate evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Awaitable, Callable
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order
from app.models.qa_verdict import QaDecision, QaVerdict
from app.schemas.qa import StrictQaResponse
from app.services.job_lease_service import JobLease, require_current_generation_fence
from app.services.media_asset_service import create_provider_grant
from app.services.partner_invite_service import require_partner_generation_allowed
from app.services.qa_service import strict_output_verdict
from app.services.storage import PrivateObjectStore, storage_service


_HARD_REJECT_REASONS = frozenset({"nsfw"})
_INFRASTRUCTURE_REASONS = frozenset(
    {
        "vision_error",
        "vision_schema_invalid",
        "qa_strict_runtime_disabled",
        "qa_local_checker_unavailable",
        "qa_source_identity_missing",
        "identity_embedding_unavailable",
        "photometric_qa_unavailable",
    }
)


class QaVerdictError(RuntimeError):
    pass


class QaInfrastructureError(QaVerdictError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(reasons[0] if reasons else "qa_infrastructure_unavailable")


@dataclass(frozen=True, slots=True)
class QaSnapshot:
    job: GenerationJob
    attempt: GenerationAttempt
    order: Order
    candidate: MediaAsset
    source_assets: tuple[MediaAsset, ...]
    existing_verdict: QaVerdict | None


def _source_ids(order: Order) -> tuple[uuid.UUID, ...]:
    values = order.source_asset_ids
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise QaVerdictError("qa_source_lineage_invalid")
    try:
        parsed = tuple(uuid.UUID(str(value)) for value in values)
    except (TypeError, ValueError, AttributeError) as exc:
        raise QaVerdictError("qa_source_lineage_invalid") from exc
    if len(set(parsed)) != len(parsed):
        raise QaVerdictError("qa_source_lineage_invalid")
    return parsed


async def _snapshot_qa_context(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    now: datetime,
) -> QaSnapshot:
    await require_partner_generation_allowed(db, job_id=lease.job_id)
    job = await require_current_generation_fence(
        db,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        claim_id=lease.claim_id,
        fencing_token=lease.fencing_token,
        now=now,
    )
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if (
        attempt is None
        or attempt.job_id != job.id
        or job.active_attempt_id != attempt.id
        or GenerationAttemptStatus(attempt.status) is not GenerationAttemptStatus.FINISHED
        or attempt.result_asset_id is None
    ):
        raise QaVerdictError("qa_attempt_lineage_invalid")
    order = await db.scalar(
        select(Order).where(Order.id == job.order_id).with_for_update()
    )
    if order is None or order.generation_job_id != job.id:
        raise QaVerdictError("qa_order_lineage_invalid")
    candidate = await db.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == attempt.result_asset_id)
        .with_for_update()
    )
    if (
        candidate is None
        or candidate.owner_user_id != order.user_id
        or candidate.order_id != order.id
        or candidate.job_id != job.id
        or MediaAssetRole(candidate.role) is not MediaAssetRole.CANDIDATE
        or MediaAssetStatus(candidate.status) is not MediaAssetStatus.ACTIVE
        or candidate.read_revoked_at is not None
        or candidate.expires_at <= now
    ):
        raise QaVerdictError("qa_candidate_lineage_invalid")
    source_ids = _source_ids(order)
    sources = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(source_ids))
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {asset.id: asset for asset in sources}
    if set(by_id) != set(source_ids):
        raise QaVerdictError("qa_source_lineage_invalid")
    ordered_sources = tuple(by_id[source_id] for source_id in source_ids)
    for source in ordered_sources:
        if (
            source.owner_user_id != order.user_id
            or source.order_id != order.id
            or source.job_id != job.id
            or MediaAssetRole(source.role) is not MediaAssetRole.SOURCE
            or MediaAssetStatus(source.status) is not MediaAssetStatus.ACTIVE
            or source.read_revoked_at is not None
            or source.expires_at <= now
        ):
            raise QaVerdictError("qa_source_lineage_invalid")
    existing = await db.scalar(
        select(QaVerdict).where(
            QaVerdict.attempt_id == attempt.id,
            QaVerdict.candidate_asset_id == candidate.id,
        )
    )
    return QaSnapshot(
        job=job,
        attempt=attempt,
        order=order,
        candidate=candidate,
        source_assets=ordered_sources,
        existing_verdict=existing,
    )


def _decision_for_response(response: StrictQaResponse) -> QaDecision:
    if response.passed:
        return QaDecision.PASS
    if _HARD_REJECT_REASONS.intersection(response.reason_codes):
        return QaDecision.REJECT
    return QaDecision.REPAIR


def build_qa_verdict(
    *,
    snapshot: QaSnapshot,
    response: StrictQaResponse,
    decision_override: QaDecision | None = None,
) -> QaVerdict:
    serialized = json.dumps(
        response.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = response.checks
    return QaVerdict(
        id=uuid.uuid4(),
        job_id=snapshot.job.id,
        attempt_id=snapshot.attempt.id,
        candidate_asset_id=snapshot.candidate.id,
        checker_version=response.checker_version,
        model_version=response.model_version,
        schema_version=response.schema_version,
        decision=decision_override or _decision_for_response(response),
        reasons=list(response.reason_codes),
        metrics={
            "scores": {
                name: getattr(checks, name).score
                for name in (
                    "technical",
                    "identity",
                    "subject",
                    "safety",
                    "style",
                    "composition",
                    "exposure",
                    "watermark",
                )
            }
        },
        response_sha256=hashlib.sha256(serialized).hexdigest(),
    )


def _template_style_context(order: Order) -> str:
    snapshot = order.product_policy_snapshot
    if not isinstance(snapshot, dict):
        return str(order.template_id or "")[:1400]
    fields = (
        snapshot.get("template_id"),
        snapshot.get("global_style_text"),
        snapshot.get("scene_text"),
        snapshot.get("outfit_text"),
    )
    return " | ".join(str(value).strip() for value in fields if str(value or "").strip())[:1400]


async def run_and_persist_strict_qa(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    lease: JobLease,
    evaluator: Callable[..., Awaitable[StrictQaResponse]] = strict_output_verdict,
    object_store: PrivateObjectStore = storage_service,
    persist_infrastructure_failure: bool = False,
    now: datetime | None = None,
) -> QaVerdict:
    """Run every QA dependency and append one verdict only under a fresh fence."""

    current = now or datetime.now(timezone.utc)
    snapshot = await _snapshot_qa_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    if snapshot.existing_verdict is not None:
        return snapshot.existing_verdict
    candidate_bytes = await asyncio.to_thread(
        object_store.read_private,
        snapshot.candidate.object_key,
    )
    snapshot = await _snapshot_qa_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    if (
        len(candidate_bytes) != int(snapshot.candidate.byte_size)
        or hashlib.sha256(candidate_bytes).hexdigest() != snapshot.candidate.sha256
    ):
        raise QaVerdictError("qa_candidate_integrity_failed")

    issued = []
    for asset in (snapshot.candidate, *snapshot.source_assets):
        issued.append(
            await create_provider_grant(
                db,
                asset=asset,
                provider="qa-runtime",
                purpose="strict-qa-input",
                job_id=snapshot.job.id,
                attempt_id=snapshot.attempt.id,
                commit=False,
                now=current,
            )
        )
    await db.commit()
    snapshot = await _snapshot_qa_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    response = await evaluator(
        candidate_bytes,
        candidate_url=issued[0].read_url,
        source_image_urls=[item.read_url for item in issued[1:]],
        is_couple=len(snapshot.source_assets) == 2,
        template_style_context=_template_style_context(snapshot.order),
    )
    if not isinstance(response, StrictQaResponse):
        raise QaVerdictError("qa_response_contract_invalid")
    snapshot = await _snapshot_qa_context(
        db,
        attempt_id=attempt_id,
        lease=lease,
        now=current,
    )
    for item in issued:
        item.grant.revoked_at = current
    if snapshot.existing_verdict is not None:
        await db.commit()
        return snapshot.existing_verdict
    infrastructure = tuple(
        reason for reason in response.reason_codes if reason in _INFRASTRUCTURE_REASONS
    )
    if infrastructure and not persist_infrastructure_failure:
        await db.commit()
        raise QaInfrastructureError(infrastructure)
    verdict = build_qa_verdict(
        snapshot=snapshot,
        response=response,
        decision_override=(QaDecision.REJECT if infrastructure else None),
    )
    db.add(verdict)
    await db.flush()
    await db.commit()
    return verdict
