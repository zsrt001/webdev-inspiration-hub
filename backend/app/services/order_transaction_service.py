"""Single-transaction order admission into the durable generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.schemas.order import AcceptedOrder
from app.services.credit_reservation_service import (
    CreditFundingLock,
    OrderFundingPolicySnapshot,
    reserve_credits,
)
from app.services.idempotency_service import (
    IdempotencyConflict,
    begin_idempotent_request,
    complete_idempotent_request,
)


settings = get_settings()


class OrderTransactionError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class OrderPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_version: str = "order-policy.v1"
    template_id: str = Field(min_length=1, max_length=64)
    product_code: str = Field(min_length=1, max_length=64)
    catalog_version_id: uuid.UUID
    catalog_version: str = Field(min_length=1, max_length=64)
    catalog_release_sha: str = Field(min_length=7, max_length=64)
    generation_mode: str = Field(min_length=1, max_length=64)
    scene_tier: str = Field(min_length=1, max_length=32)
    subject_count: int = Field(ge=1, le=2)
    director_mode: bool
    credit_cost: int = Field(gt=0, le=10000)
    gatekeeper_policy_version: str = Field(min_length=1, max_length=64)
    gatekeeper_passed: bool
    global_style_text: str | None = Field(default=None, max_length=1000)
    scene_text: str | None = Field(default=None, max_length=1000)
    outfit_text: str | None = Field(default=None, max_length=1000)
    scene_preset_id: str | None = Field(default=None, max_length=128)
    clothing_preset_id: str | None = Field(default=None, max_length=128)
    prompt_override: str | None = Field(default=None, max_length=1000)


@dataclass(frozen=True, slots=True)
class RuntimeExecutionStamp:
    api_deployment_id: str
    runtime_bundle_id: str
    worker_image_digest: str


@dataclass(frozen=True, slots=True)
class CreateOrderCommand:
    user_id: uuid.UUID
    idempotency_key: str
    request_hash: str
    asset_ids: tuple[uuid.UUID, ...]
    product_policy: OrderPolicySnapshot
    funding_policy: OrderFundingPolicySnapshot
    credit_cost: int
    asset_owner_ids: tuple[uuid.UUID, ...] | None = None
    idempotency_endpoint: str = "orders.create"


@dataclass(frozen=True, slots=True)
class OrderTransactionGraph:
    accepted: AcceptedOrder
    order: Order | None
    reservation: object | None
    job: GenerationJob | None
    outbox: OutboxEvent | None


def require_server_runtime_execution_stamp() -> RuntimeExecutionStamp:
    deployment_id = str(settings.vercel_deployment_id or "").strip()
    runtime_bundle_id = str(settings.runtime_bundle_id or "").strip().lower()
    worker_digest = str(settings.worker_image_digest or "").strip().lower()
    if not deployment_id or len(deployment_id) > 128:
        raise OrderTransactionError("api_deployment_id_missing", status_code=503)
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime_bundle_id):
        raise OrderTransactionError("runtime_bundle_id_invalid", status_code=503)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", worker_digest):
        raise OrderTransactionError("worker_image_digest_invalid", status_code=503)
    return RuntimeExecutionStamp(
        api_deployment_id=deployment_id,
        runtime_bundle_id=runtime_bundle_id,
        worker_image_digest=worker_digest,
    )


def _validate_command(command: CreateOrderCommand) -> None:
    if not isinstance(command.user_id, uuid.UUID):
        raise OrderTransactionError("order_user_id_invalid", status_code=400)
    if not command.idempotency_key or len(command.idempotency_key) > 128:
        raise OrderTransactionError("idempotency_key_invalid", status_code=400)
    if not re.fullmatch(r"[0-9a-f]{64}", str(command.request_hash or "")):
        raise OrderTransactionError("order_request_hash_invalid", status_code=400)
    if not 1 <= len(command.asset_ids) <= 2 or len(set(command.asset_ids)) != len(command.asset_ids):
        raise OrderTransactionError("order_asset_ids_invalid", status_code=422)
    if int(command.credit_cost) <= 0:
        raise OrderTransactionError("order_credit_cost_invalid", status_code=422)
    if command.product_policy.credit_cost != int(command.credit_cost):
        raise OrderTransactionError("order_credit_cost_snapshot_mismatch", status_code=422)
    if command.product_policy.gatekeeper_passed is not True:
        raise OrderTransactionError("order_gatekeeper_not_passed", status_code=422)
    if command.product_policy.subject_count != len(command.asset_ids):
        raise OrderTransactionError("order_subject_count_mismatch", status_code=422)
    if command.funding_policy.subject_count != len(command.asset_ids):
        raise OrderTransactionError("funding_subject_count_mismatch", status_code=422)
    if command.asset_owner_ids is not None and (
        len(command.asset_owner_ids) != len(command.asset_ids)
        or command.asset_owner_ids[0] != command.user_id
    ):
        raise OrderTransactionError("order_asset_owner_ids_invalid", status_code=422)
    if not str(command.idempotency_endpoint or "").strip() or len(command.idempotency_endpoint) > 128:
        raise OrderTransactionError("order_idempotency_endpoint_invalid", status_code=400)


async def _lock_owned_active_assets(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    asset_ids: tuple[uuid.UUID, ...],
    owner_user_ids: tuple[uuid.UUID, ...] | None = None,
) -> list[MediaAsset]:
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(asset_ids))
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {item.id: item for item in assets}
    if set(by_id) != set(asset_ids):
        raise OrderTransactionError("order_source_asset_not_found", status_code=404)
    ordered = [by_id[item] for item in asset_ids]
    expected_owners = owner_user_ids or tuple(user_id for _ in asset_ids)
    if any(
        item.owner_user_id != expected_owner
        or str(item.status.value if hasattr(item.status, "value") else item.status)
        != MediaAssetStatus.ACTIVE.value
        or str(item.role.value if hasattr(item.role, "value") else item.role)
        != MediaAssetRole.SOURCE.value
        or item.read_revoked_at is not None
        for item, expected_owner in zip(ordered, expected_owners, strict=True)
    ):
        raise OrderTransactionError("order_source_asset_not_active", status_code=409)
    return ordered


async def create_order_transaction_graph(
    db: AsyncSession,
    command: CreateOrderCommand,
    *,
    now: datetime | None = None,
    prelocked_assets: tuple[MediaAsset, ...] | list[MediaAsset] | None = None,
    funding_lock: CreditFundingLock | None = None,
) -> OrderTransactionGraph:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("order_transaction_now_must_be_timezone_aware")
    _validate_command(command)
    attempt = await begin_idempotent_request(
        db,
        user_id=command.user_id,
        endpoint=command.idempotency_endpoint,
        key=command.idempotency_key,
        request_hash=command.request_hash,
        now=current,
    )
    if attempt.replayed:
        if attempt.response_status == 202 and attempt.response_json is not None:
            return OrderTransactionGraph(
                accepted=AcceptedOrder.model_validate(attempt.response_json, strict=False),
                order=None,
                reservation=None,
                job=None,
                outbox=None,
            )
        raise OrderTransactionError("order_idempotency_in_progress")
    runtime = require_server_runtime_execution_stamp()

    assets = list(prelocked_assets or ())
    if not assets:
        assets = await _lock_owned_active_assets(
            db,
            user_id=command.user_id,
            asset_ids=command.asset_ids,
            owner_user_ids=command.asset_owner_ids,
        )
    elif tuple(item.id for item in assets) != command.asset_ids:
        raise OrderTransactionError("order_prelocked_assets_invalid", status_code=409)
    order = Order(
        id=uuid.uuid4(),
        user_id=command.user_id,
        status=OrderStatus.QUEUED,
        template_id=command.product_policy.template_id,
        source_asset_ids=[str(item) for item in command.asset_ids],
        product_policy_snapshot=command.product_policy.model_dump(mode="json"),
        funding_policy_snapshot=command.funding_policy.model_dump(mode="json"),
        settlement_status="RESERVED",
        delivery_status="PENDING",
        price_cents=0,
    )
    db.add(order)
    await db.flush()
    reservation = await reserve_credits(
        db,
        user_id=command.user_id,
        order_id=order.id,
        amount=int(command.credit_cost),
        funding_policy=command.funding_policy,
        idempotency_key=f"order:{attempt.record_id}",
        now=current,
        funding_lock=funding_lock,
    )
    job = GenerationJob.queued(
        order_id=order.id,
        submission_correlation_id=uuid.uuid4(),
        api_deployment_id=runtime.api_deployment_id,
        runtime_bundle_id=runtime.runtime_bundle_id,
        expected_worker_image_digest=runtime.worker_image_digest,
    )
    db.add(job)
    await db.flush()
    order.reservation_id = reservation.id
    order.generation_job_id = job.id
    outbox = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="generation_job",
        aggregate_id=job.id,
        event_type="GENERATION_JOB_CREATED",
        dedupe_key=f"generation:v1:{job.id}",
        payload_version="generation-job.v1",
        payload_json={"job_id": str(job.id), "payload_version": "generation-job.v1"},
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        next_attempt_at=current,
        fencing_token=0,
    )
    db.add(outbox)
    accepted = AcceptedOrder(
        order_id=order.id,
        status="QUEUED",
        status_url=f"/api/v1/orders/{order.id}",
    )
    await complete_idempotent_request(
        db,
        record_id=attempt.record_id,
        response_status=202,
        response_json=accepted.model_dump(mode="json"),
    )
    await db.flush()
    return OrderTransactionGraph(
        accepted=accepted,
        order=order,
        reservation=reservation,
        job=job,
        outbox=outbox,
    )


async def create_order_transaction(
    db: AsyncSession,
    command: CreateOrderCommand,
    *,
    now: datetime | None = None,
) -> AcceptedOrder:
    graph = await create_order_transaction_graph(db, command, now=now)
    return graph.accepted
