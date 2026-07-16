"""Authenticated Partner Invite state, consent, and withdrawal policy."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import hmac
import secrets
import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.idempotency_record import IdempotencyRecord, IdempotencyState
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.order import Order, OrderStatus
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.outbox_event import OutboxEvent
from app.models.partner_consent_case import (
    PartnerConsentCase,
    PartnerConsentCaseStatus,
)
from app.models.partner_invite import PartnerInvite, PartnerInviteStatus
from app.models.partner_invite_event import PartnerInviteEvent
from app.models.user_identity import UserIdentity
from app.schemas.order import AcceptedOrder, OrderCreate
from app.schemas.partner_invite import (
    PartnerInviteCreateRead,
    PartnerInviteSnapshot,
    PartnerWithdrawalRead,
)
from app.services.idempotency_service import canonical_request_hash
from app.services.credit_reservation_service import (
    lock_credit_funding,
    refund_captured_reservation,
    release_reservation,
)
from app.services import gatekeeper_service
from app.services.order_creation_service import build_create_order_command, _load_admission_facts
from app.services.order_transaction_service import (
    CreateOrderCommand,
    create_order_transaction_graph,
)
from app.services.generation_job_service import (
    validate_attempt_transition,
    validate_job_transition,
)
from app.services.template_service import get_template_by_id, template_is_commercial


PARTNER_INVITE_TTL_SECONDS = 86400
PARTNER_CONSENT_POLICY_VERSION = "partner-consent.v1"


class PartnerInviteError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PartnerActorRole(StrEnum):
    HOST = "HOST"
    PARTNER = "PARTNER"


class PartnerInviteCommand(StrEnum):
    CREATE = "CREATE"
    ACCEPT = "ACCEPT"
    CONSENT = "CONSENT"
    COMPLETE_ORDER = "COMPLETE_ORDER"
    REVOKE = "REVOKE"
    WITHDRAW = "WITHDRAW"
    EXPIRE = "EXPIRE"


class PartnerOrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_version: str = PARTNER_CONSENT_POLICY_VERSION
    order_intent_id: uuid.UUID
    invite_id: uuid.UUID
    host_user_id: uuid.UUID
    purpose: str = "COUPLE"
    allowed_subject_roles: tuple[str, str] = ("host", "partner")
    template_id: str

    def canonical_hash(self) -> str:
        return canonical_request_hash(self.model_dump(mode="json"))


@dataclass(frozen=True, slots=True)
class PartnerWithdrawalPlan:
    requires_case: bool
    delete_partner_source: bool
    release_reserved: bool
    request_provider_cancel: bool
    refund_captured: bool
    revoke_downloads: bool
    delete_derived_assets: bool


def _hmac_key(value: bytes) -> bytes:
    key = bytes(value)
    if len(key) < 32:
        raise PartnerInviteError("partner_invite_hmac_key_invalid", status_code=503)
    return key


def _decode_token(value: str) -> bytes:
    token = str(value or "").strip()
    try:
        raw = base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PartnerInviteError("partner_invite_token_invalid", status_code=404) from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != token:
        raise PartnerInviteError("partner_invite_token_invalid", status_code=404)
    return raw


def hash_partner_invite_token(token: str, *, hmac_key: bytes) -> str:
    raw = _decode_token(token)
    return hmac.new(
        _hmac_key(hmac_key),
        b"vowpic:partner-invite:v1:" + raw,
        hashlib.sha256,
    ).hexdigest()


def generate_partner_invite_token(*, hmac_key: bytes) -> tuple[str, str]:
    raw = secrets.token_bytes(32)
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return token, hash_partner_invite_token(token, hmac_key=hmac_key)


def build_partner_order_intent(
    *,
    invite_id: uuid.UUID,
    host_user_id: uuid.UUID,
    order_intent_id: uuid.UUID,
    template_id: str,
) -> PartnerOrderIntent:
    clean_template_id = str(template_id or "").strip()
    template = get_template_by_id(clean_template_id)
    if (
        template is None
        or not template_is_commercial(template)
        or str(template.category).strip().lower() != "couple"
    ):
        raise PartnerInviteError("partner_invite_template_invalid", status_code=422)
    return PartnerOrderIntent(
        invite_id=invite_id,
        host_user_id=host_user_id,
        order_intent_id=order_intent_id,
        template_id=clean_template_id,
    )


def validate_verified_partner_identities(host_identity: object, partner_identity: object) -> None:
    for identity in (host_identity, partner_identity):
        if (
            str(getattr(identity, "provider", "")) != "supabase"
            or not str(getattr(identity, "verified_email_snapshot", "") or "").strip()
            or getattr(identity, "revoked_at", None) is not None
        ):
            raise PartnerInviteError("partner_identity_not_verified", status_code=403)
    if (
        getattr(host_identity, "id", None) == getattr(partner_identity, "id", None)
        or getattr(host_identity, "user_id", None) == getattr(partner_identity, "user_id", None)
    ):
        raise PartnerInviteError("partner_identity_not_distinct", status_code=409)


def authorize_partner_transition(
    *,
    status: PartnerInviteStatus | str,
    actor_role: PartnerActorRole | str,
    command: PartnerInviteCommand | str,
    expires_at: datetime,
    now: datetime,
) -> PartnerInviteStatus:
    current = PartnerInviteStatus(status)
    role = PartnerActorRole(actor_role)
    action = PartnerInviteCommand(command)
    if now.tzinfo is None or expires_at.tzinfo is None:
        raise PartnerInviteError("partner_invite_time_invalid")
    if now.astimezone(timezone.utc) >= expires_at.astimezone(timezone.utc):
        raise PartnerInviteError("partner_invite_expired", status_code=410)
    transitions = {
        (PartnerInviteStatus.CREATED, PartnerActorRole.PARTNER, PartnerInviteCommand.ACCEPT): PartnerInviteStatus.ACCEPTED,
        (PartnerInviteStatus.ACCEPTED, PartnerActorRole.PARTNER, PartnerInviteCommand.CONSENT): PartnerInviteStatus.CONSENTED,
        (PartnerInviteStatus.CONSENTED, PartnerActorRole.HOST, PartnerInviteCommand.COMPLETE_ORDER): PartnerInviteStatus.COMPLETED,
        (PartnerInviteStatus.CREATED, PartnerActorRole.HOST, PartnerInviteCommand.REVOKE): PartnerInviteStatus.REVOKED,
        (PartnerInviteStatus.ACCEPTED, PartnerActorRole.HOST, PartnerInviteCommand.REVOKE): PartnerInviteStatus.REVOKED,
        (PartnerInviteStatus.CONSENTED, PartnerActorRole.HOST, PartnerInviteCommand.REVOKE): PartnerInviteStatus.REVOKED,
        (PartnerInviteStatus.ACCEPTED, PartnerActorRole.PARTNER, PartnerInviteCommand.WITHDRAW): PartnerInviteStatus.CANCELLED,
        (PartnerInviteStatus.CONSENTED, PartnerActorRole.PARTNER, PartnerInviteCommand.WITHDRAW): PartnerInviteStatus.CANCELLED,
    }
    target = transitions.get((current, role, action))
    if target is None:
        raise PartnerInviteError("partner_invite_transition_invalid")
    return target


def plan_partner_withdrawal(
    *,
    order_status: OrderStatus | str | None,
    job_status: GenerationJobStatus | str | None,
    settlement_status: str | None,
    successful_final_downloads: int,
    attempt_statuses: list[GenerationAttemptStatus | str] | None = None,
) -> PartnerWithdrawalPlan:
    downloads = int(successful_final_downloads)
    if downloads < 0:
        raise PartnerInviteError("partner_download_count_invalid")
    if order_status is None and job_status is None:
        return PartnerWithdrawalPlan(False, True, False, False, False, False, False)
    if order_status is None or job_status is None:
        raise PartnerInviteError("partner_withdrawal_lineage_invalid")
    order_state = OrderStatus(order_status)
    job_state = GenerationJobStatus(job_status)
    settlement = str(settlement_status or "")
    attempts = {
        GenerationAttemptStatus(item)
        for item in (attempt_statuses or [])
    }
    provider_possible = bool(
        attempts
        & {
            GenerationAttemptStatus.SUBMITTING,
            GenerationAttemptStatus.SUBMITTED,
            GenerationAttemptStatus.UNKNOWN,
        }
    ) or job_state is GenerationJobStatus.RECONCILING
    release = bool(
        settlement == "RESERVED"
        and job_state in {GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE}
        and not provider_possible
    )
    request_cancel = provider_possible
    ready = order_state is OrderStatus.READY and job_state is GenerationJobStatus.FINISHED
    return PartnerWithdrawalPlan(
        requires_case=True,
        delete_partner_source=True,
        release_reserved=release,
        request_provider_cancel=request_cancel,
        refund_captured=bool(ready and settlement == "CAPTURED" and downloads == 0),
        revoke_downloads=True,
        delete_derived_assets=True,
    )


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise PartnerInviteError("partner_invite_time_invalid")
    return current.astimezone(timezone.utc)


def _request_id(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128:
        raise PartnerInviteError("partner_invite_request_id_invalid", status_code=400)
    return clean


async def _lock_verified_identity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> UserIdentity:
    identity = await db.scalar(
        select(UserIdentity)
        .where(UserIdentity.user_id == user_id, UserIdentity.revoked_at.is_(None))
        .with_for_update()
    )
    if (
        identity is None
        or identity.provider != "supabase"
        or not str(identity.verified_email_snapshot or "").strip()
    ):
        raise PartnerInviteError("partner_identity_not_verified", status_code=403)
    return identity


async def _lock_partner_invite(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
) -> PartnerInvite:
    invite = await db.scalar(
        select(PartnerInvite).where(PartnerInvite.id == invite_id).with_for_update()
    )
    if invite is None:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    return invite


async def _lock_invite_by_token(
    db: AsyncSession,
    *,
    token: str,
    hmac_key: bytes,
) -> PartnerInvite:
    token_hash = hash_partner_invite_token(token, hmac_key=hmac_key)
    invite = await db.scalar(
        select(PartnerInvite)
        .where(PartnerInvite.token_hash == token_hash)
        .with_for_update()
    )
    if invite is None:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    return invite


async def _lock_active_source_asset(
    db: AsyncSession,
    *,
    asset_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> MediaAsset:
    asset = await db.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if (
        asset is None
        or asset.owner_user_id != owner_user_id
        or _value(asset.role) != MediaAssetRole.SOURCE.value
        or _value(asset.status) != MediaAssetStatus.ACTIVE.value
        or asset.read_revoked_at is not None
    ):
        raise PartnerInviteError("partner_source_asset_not_active", status_code=404)
    return asset


def _actor_role(invite: PartnerInvite | object, user_id: uuid.UUID) -> PartnerActorRole:
    if invite.host_user_id == user_id:
        return PartnerActorRole.HOST
    if invite.partner_user_id == user_id:
        return PartnerActorRole.PARTNER
    raise PartnerInviteError("partner_invite_not_found", status_code=404)


def _snapshot(invite: PartnerInvite | object, role: PartnerActorRole) -> PartnerInviteSnapshot:
    return PartnerInviteSnapshot(
        id=invite.id,
        purpose=invite.purpose,
        status=_value(invite.status),
        role=role.value,
        version=int(invite.version),
        expires_at=invite.expires_at,
        order_intent_id=invite.order_intent_id,
        order_intent_hash=invite.order_intent_hash,
        intent_policy_version=invite.intent_policy_version,
        template_id=invite.template_id,
        consent_event_id=invite.consent_event_id,
        order_id=invite.order_id if role is PartnerActorRole.HOST else None,
    )


def _audit_transition(
    db: AsyncSession,
    *,
    invite: PartnerInvite | object,
    actor_user_id: uuid.UUID,
    actor_role: PartnerActorRole,
    command: PartnerInviteCommand,
    from_status: str,
    request_id: str,
    event_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> PartnerInviteEvent:
    event = PartnerInviteEvent(
        id=event_id or uuid.uuid4(),
        invite_id=invite.id,
        actor_user_id=actor_user_id,
        actor_role=actor_role.value,
        command=command.value,
        from_status=from_status,
        to_status=_value(invite.status),
        invite_version=int(invite.version),
        request_id=_request_id(request_id),
        details_json=dict(details) if details else None,
    )
    db.add(event)
    return event


async def require_partner_generation_allowed(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> PartnerInvite | None:
    """Lock Partner consent before a generation stage may commit new facts."""

    invite = await db.scalar(
        select(PartnerInvite)
        .where(PartnerInvite.job_id == job_id)
        .with_for_update()
    )
    if invite is None:
        return None
    if PartnerInviteStatus(invite.status) is not PartnerInviteStatus.COMPLETED:
        raise PartnerInviteError("generation_partner_consent_invalid", status_code=409)
    consent_case_id = await db.scalar(
        select(PartnerConsentCase.id)
        .where(PartnerConsentCase.invite_id == invite.id)
        .limit(1)
    )
    if consent_case_id is not None:
        raise PartnerInviteError("generation_partner_consent_withdrawn", status_code=409)
    return invite


async def create_partner_invite(
    db: AsyncSession,
    *,
    host_user_id: uuid.UUID,
    hmac_key: bytes,
    frontend_base_url: str,
    template_id: str,
    request_id: str,
    now: datetime | None = None,
) -> PartnerInviteCreateRead:
    current = _now(now)
    identity = await _lock_verified_identity(db, user_id=host_user_id)
    token, token_hash = generate_partner_invite_token(hmac_key=hmac_key)
    invite_id = uuid.uuid4()
    intent = build_partner_order_intent(
        invite_id=invite_id,
        host_user_id=host_user_id,
        order_intent_id=uuid.uuid4(),
        template_id=template_id,
    )
    invite = PartnerInvite(
        id=invite_id,
        host_user_id=host_user_id,
        host_identity_id=identity.id,
        token_hash=token_hash,
        purpose=intent.purpose,
        order_intent_id=intent.order_intent_id,
        order_intent_hash=intent.canonical_hash(),
        intent_policy_version=intent.policy_version,
        template_id=intent.template_id,
        status=PartnerInviteStatus.CREATED,
        expires_at=current + timedelta(seconds=PARTNER_INVITE_TTL_SECONDS),
        version=1,
        created_at=current,
        updated_at=current,
    )
    db.add(invite)
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=host_user_id,
        actor_role=PartnerActorRole.HOST,
        command=PartnerInviteCommand.CREATE,
        from_status="NONE",
        request_id=request_id,
    )
    await db.flush()
    base = str(frontend_base_url or "").strip().rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://localhost"):
        raise PartnerInviteError("partner_invite_frontend_url_invalid", status_code=503)
    return PartnerInviteCreateRead(
        invite=_snapshot(invite, PartnerActorRole.HOST),
        token=token,
        join_url=f"{base}/join?token={token}",
    )


async def accept_partner_invite(
    db: AsyncSession,
    *,
    token: str,
    partner_user_id: uuid.UUID,
    hmac_key: bytes,
    request_id: str,
    now: datetime | None = None,
) -> PartnerInviteSnapshot:
    current = _now(now)
    invite = await _lock_invite_by_token(db, token=token, hmac_key=hmac_key)
    partner_identity = await _lock_verified_identity(db, user_id=partner_user_id)
    host_identity = await _lock_verified_identity(db, user_id=invite.host_user_id)
    validate_verified_partner_identities(host_identity, partner_identity)
    target = authorize_partner_transition(
        status=invite.status,
        actor_role=PartnerActorRole.PARTNER,
        command=PartnerInviteCommand.ACCEPT,
        expires_at=invite.expires_at,
        now=current,
    )
    before = _value(invite.status)
    invite.partner_user_id = partner_user_id
    invite.partner_identity_id = partner_identity.id
    invite.status = target
    invite.accepted_at = current
    invite.version = int(invite.version) + 1
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=partner_user_id,
        actor_role=PartnerActorRole.PARTNER,
        command=PartnerInviteCommand.ACCEPT,
        from_status=before,
        request_id=request_id,
    )
    await db.flush()
    return _snapshot(invite, PartnerActorRole.PARTNER)


async def consent_partner_invite(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    partner_user_id: uuid.UUID,
    expected_version: int,
    order_intent_id: uuid.UUID,
    order_intent_hash: str,
    partner_asset_id: uuid.UUID,
    request_id: str,
    now: datetime | None = None,
) -> PartnerInviteSnapshot:
    current = _now(now)
    invite = await _lock_partner_invite(db, invite_id=invite_id)
    if _actor_role(invite, partner_user_id) is not PartnerActorRole.PARTNER:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    if int(invite.version) != int(expected_version):
        raise PartnerInviteError("partner_invite_version_conflict", status_code=409)
    if invite.order_intent_id != order_intent_id or invite.order_intent_hash != order_intent_hash:
        raise PartnerInviteError("partner_invite_intent_conflict", status_code=409)
    target = authorize_partner_transition(
        status=invite.status,
        actor_role=PartnerActorRole.PARTNER,
        command=PartnerInviteCommand.CONSENT,
        expires_at=invite.expires_at,
        now=current,
    )
    asset = await _lock_active_source_asset(
        db, asset_id=partner_asset_id, owner_user_id=partner_user_id
    )
    before = _value(invite.status)
    event_id = uuid.uuid4()
    invite.partner_asset_id = asset.id
    invite.partner_asset_sha256 = asset.sha256
    invite.consent_event_id = event_id
    invite.status = target
    invite.consented_at = current
    invite.version = int(invite.version) + 1
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=partner_user_id,
        actor_role=PartnerActorRole.PARTNER,
        command=PartnerInviteCommand.CONSENT,
        from_status=before,
        request_id=request_id,
        event_id=event_id,
        details={"partner_asset_id": str(asset.id), "partner_asset_sha256": asset.sha256},
    )
    await db.flush()
    return _snapshot(invite, PartnerActorRole.PARTNER)


async def _lock_consent_event(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    consent_event_id: uuid.UUID,
) -> PartnerInviteEvent:
    event = await db.scalar(
        select(PartnerInviteEvent)
        .where(PartnerInviteEvent.id == consent_event_id)
        .with_for_update()
    )
    if (
        event is None
        or event.invite_id != invite_id
        or event.command != PartnerInviteCommand.CONSENT.value
    ):
        raise PartnerInviteError("partner_consent_event_invalid", status_code=409)
    return event


async def _lock_partner_order_assets(
    db: AsyncSession,
    *,
    invite: PartnerInvite | object,
    host_asset_id: uuid.UUID,
) -> tuple[MediaAsset, MediaAsset]:
    if invite.partner_asset_id is None or host_asset_id == invite.partner_asset_id:
        raise PartnerInviteError("partner_order_assets_invalid", status_code=422)
    requested = (host_asset_id, invite.partner_asset_id)
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(requested))
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    by_id = {item.id: item for item in assets}
    if set(by_id) != set(requested):
        raise PartnerInviteError("partner_order_asset_not_found", status_code=404)
    host_asset = by_id[host_asset_id]
    partner_asset = by_id[invite.partner_asset_id]
    expected = (
        (host_asset, invite.host_user_id, None),
        (partner_asset, invite.partner_user_id, invite.partner_asset_sha256),
    )
    if any(
        asset.owner_user_id != owner_id
        or _value(asset.role) != MediaAssetRole.SOURCE.value
        or _value(asset.status) != MediaAssetStatus.ACTIVE.value
        or asset.read_revoked_at is not None
        or (checksum is not None and asset.sha256 != checksum)
        for asset, owner_id, checksum in expected
    ):
        raise PartnerInviteError("partner_order_asset_lineage_invalid", status_code=409)
    return host_asset, partner_asset


async def _completed_partner_order_replay(
    db: AsyncSession,
    *,
    host_user_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> AcceptedOrder | None:
    record = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == host_user_id,
            IdempotencyRecord.endpoint == "partner_invites.order",
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise PartnerInviteError("idempotency_payload_mismatch", status_code=409)
    state = _value(record.state)
    if (
        state == IdempotencyState.COMPLETED.value
        and record.response_status == 202
        and record.response_json is not None
    ):
        return AcceptedOrder.model_validate(record.response_json, strict=False)
    return None


async def _build_partner_order_command(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    host_user_id: uuid.UUID,
    expected_version: int,
    host_asset_id: uuid.UUID,
    consent_event_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
    now: datetime,
) -> CreateOrderCommand:
    invite = await db.scalar(select(PartnerInvite).where(PartnerInvite.id == invite_id))
    if (
        invite is None
        or invite.host_user_id != host_user_id
        or _value(invite.status) != PartnerInviteStatus.CONSENTED.value
        or int(invite.version) != int(expected_version)
        or invite.consent_event_id != consent_event_id
        or invite.partner_user_id is None
        or invite.partner_asset_id is None
    ):
        raise PartnerInviteError("partner_invite_order_not_ready", status_code=409)
    for owner_id, asset_id in (
        (host_user_id, host_asset_id),
        (invite.partner_user_id, invite.partner_asset_id),
    ):
        result = await gatekeeper_service.check_image_quality(
            db, owner_user_id=owner_id, asset_id=asset_id
        )
        if result.passed is not True:
            raise PartnerInviteError("partner_order_gate_rejected", status_code=422)
    facts = await _load_admission_facts(db, user_id=host_user_id, now=now)
    request = OrderCreate(
        template_id=invite.template_id,
        asset_ids=[host_asset_id, invite.partner_asset_id],
        legal_accepted=True,
        director_mode=False,
    )
    command = build_create_order_command(
        request=request,
        user_id=host_user_id,
        idempotency_key=idempotency_key,
        facts=facts,
    )
    if (
        command.product_policy.generation_mode != "couple"
        or command.product_policy.subject_count != 2
        or command.funding_policy.allowed_lot_class != "PAID_ONLY"
        or command.funding_policy.is_trial
    ):
        raise PartnerInviteError("partner_order_paid_policy_invalid", status_code=409)
    return replace(
        command,
        request_hash=request_hash,
        asset_owner_ids=(host_user_id, invite.partner_user_id),
        idempotency_endpoint="partner_invites.order",
    )


async def create_partner_order(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    host_user_id: uuid.UUID,
    expected_version: int,
    host_asset_id: uuid.UUID,
    consent_event_id: uuid.UUID,
    idempotency_key: str,
    request_id: str,
    now: datetime | None = None,
) -> AcceptedOrder:
    current = _now(now)
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise PartnerInviteError("idempotency_key_invalid", status_code=400)
    request_hash = canonical_request_hash(
        {
            "invite_id": str(invite_id),
            "expected_version": int(expected_version),
            "host_asset_id": str(host_asset_id),
            "consent_event_id": str(consent_event_id),
        }
    )
    replay = await _completed_partner_order_replay(
        db,
        host_user_id=host_user_id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay
    command = await _build_partner_order_command(
        db,
        invite_id=invite_id,
        host_user_id=host_user_id,
        expected_version=expected_version,
        host_asset_id=host_asset_id,
        consent_event_id=consent_event_id,
        idempotency_key=key,
        request_hash=request_hash,
        now=current,
    )
    invite = await _lock_partner_invite(db, invite_id=invite_id)
    if (
        invite.host_user_id != host_user_id
        or int(invite.version) != int(expected_version)
        or _value(invite.status) != PartnerInviteStatus.CONSENTED.value
        or invite.consent_event_id != consent_event_id
    ):
        raise PartnerInviteError("partner_invite_order_not_ready", status_code=409)
    await _lock_consent_event(
        db, invite_id=invite.id, consent_event_id=consent_event_id
    )
    assets = await _lock_partner_order_assets(
        db, invite=invite, host_asset_id=host_asset_id
    )
    funding_lock = await lock_credit_funding(
        db,
        user_id=host_user_id,
        amount=int(command.credit_cost),
        funding_policy=command.funding_policy,
        now=current,
    )
    graph = await create_order_transaction_graph(
        db,
        command,
        now=current,
        prelocked_assets=assets,
        funding_lock=funding_lock,
    )
    if graph.order is None or graph.job is None:
        raise PartnerInviteError("partner_order_graph_missing", status_code=409)
    target = authorize_partner_transition(
        status=invite.status,
        actor_role=PartnerActorRole.HOST,
        command=PartnerInviteCommand.COMPLETE_ORDER,
        expires_at=invite.expires_at,
        now=current,
    )
    before = _value(invite.status)
    invite.order_id = graph.order.id
    invite.job_id = graph.job.id
    invite.status = target
    invite.completed_at = current
    invite.version = int(invite.version) + 1
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=host_user_id,
        actor_role=PartnerActorRole.HOST,
        command=PartnerInviteCommand.COMPLETE_ORDER,
        from_status=before,
        request_id=request_id,
        details={"order_id": str(graph.order.id), "job_id": str(graph.job.id)},
    )
    await db.flush()
    return graph.accepted


async def read_partner_invite(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    user_id: uuid.UUID,
) -> PartnerInviteSnapshot:
    invite = await db.scalar(select(PartnerInvite).where(PartnerInvite.id == invite_id))
    if invite is None:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    return _snapshot(invite, _actor_role(invite, user_id))


async def _stage_case_asset(
    db: AsyncSession,
    asset: MediaAsset,
    *,
    reason: str,
    now: datetime,
) -> None:
    status = MediaAssetStatus(asset.status)
    if status is MediaAssetStatus.DELETED:
        return
    if status is MediaAssetStatus.PENDING_UPLOAD:
        asset.status = MediaAssetStatus.UPLOAD_FAILED
        await db.flush()
        status = MediaAssetStatus.UPLOAD_FAILED
    if status in {
        MediaAssetStatus.ACTIVE,
        MediaAssetStatus.UPLOAD_FAILED,
        MediaAssetStatus.QUARANTINED,
        MediaAssetStatus.DELETE_FAILED,
    }:
        asset.status = MediaAssetStatus.PENDING_DELETE
    asset.read_revoked_at = asset.read_revoked_at or now
    asset.deletion_reason = reason
    asset.deletion_blockers = ["partner_consent_case_pending"]
    asset.next_delete_at = now


async def revoke_partner_invite(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    host_user_id: uuid.UUID,
    expected_version: int,
    request_id: str,
    now: datetime | None = None,
) -> PartnerInviteSnapshot:
    current = _now(now)
    invite = await _lock_partner_invite(db, invite_id=invite_id)
    if _actor_role(invite, host_user_id) is not PartnerActorRole.HOST:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    if int(invite.version) != int(expected_version):
        raise PartnerInviteError("partner_invite_version_conflict", status_code=409)
    target = authorize_partner_transition(
        status=invite.status,
        actor_role=PartnerActorRole.HOST,
        command=PartnerInviteCommand.REVOKE,
        expires_at=invite.expires_at,
        now=current,
    )
    if invite.partner_asset_id is not None:
        asset = await db.scalar(
            select(MediaAsset)
            .where(MediaAsset.id == invite.partner_asset_id)
            .with_for_update()
        )
        if asset is not None:
            await _stage_case_asset(
                db,
                asset,
                reason="partner_consent_revoked",
                now=current,
            )
    before = _value(invite.status)
    invite.status = target
    invite.revoked_at = current
    invite.version = int(invite.version) + 1
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=host_user_id,
        actor_role=PartnerActorRole.HOST,
        command=PartnerInviteCommand.REVOKE,
        from_status=before,
        request_id=request_id,
    )
    await db.flush()
    return _snapshot(invite, PartnerActorRole.HOST)


async def withdraw_partner_invite(
    db: AsyncSession,
    *,
    invite_id: uuid.UUID,
    partner_user_id: uuid.UUID,
    expected_version: int,
    request_id: str,
    now: datetime | None = None,
) -> PartnerWithdrawalRead:
    current = _now(now)
    invite = await _lock_partner_invite(db, invite_id=invite_id)
    if _actor_role(invite, partner_user_id) is not PartnerActorRole.PARTNER:
        raise PartnerInviteError("partner_invite_not_found", status_code=404)
    if int(invite.version) != int(expected_version):
        raise PartnerInviteError("partner_invite_version_conflict", status_code=409)
    status = PartnerInviteStatus(invite.status)
    if status in {PartnerInviteStatus.ACCEPTED, PartnerInviteStatus.CONSENTED}:
        target = authorize_partner_transition(
            status=status,
            actor_role=PartnerActorRole.PARTNER,
            command=PartnerInviteCommand.WITHDRAW,
            expires_at=invite.expires_at,
            now=current,
        )
        if invite.partner_asset_id is not None:
            asset = await db.scalar(
                select(MediaAsset)
                .where(MediaAsset.id == invite.partner_asset_id)
                .with_for_update()
            )
            if asset is not None:
                await _stage_case_asset(
                    db,
                    asset,
                    reason="partner_consent_withdrawn",
                    now=current,
                )
        before = _value(invite.status)
        invite.status = target
        invite.cancelled_at = current
        invite.version = int(invite.version) + 1
        _audit_transition(
            db,
            invite=invite,
            actor_user_id=partner_user_id,
            actor_role=PartnerActorRole.PARTNER,
            command=PartnerInviteCommand.WITHDRAW,
            from_status=before,
            request_id=request_id,
        )
        await db.flush()
        return PartnerWithdrawalRead(
            invite_id=invite.id,
            invite_status=target.value,
        )
    if status is not PartnerInviteStatus.COMPLETED or invite.order_id is None or invite.job_id is None:
        raise PartnerInviteError("partner_invite_transition_invalid", status_code=409)

    existing = await db.scalar(
        select(PartnerConsentCase)
        .where(PartnerConsentCase.invite_id == invite.id)
        .with_for_update()
    )
    if existing is not None:
        return PartnerWithdrawalRead(
            invite_id=invite.id,
            invite_status=status.value,
            case_id=existing.id,
            case_status=_value(existing.status),
            order_status=None,
        )
    order = await db.scalar(select(Order).where(Order.id == invite.order_id).with_for_update())
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == invite.job_id).with_for_update()
    )
    if order is None or job is None or order.generation_job_id != job.id:
        raise PartnerInviteError("partner_withdrawal_lineage_invalid", status_code=409)
    successful_downloads = int(
        await db.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.aggregate_type == "order_download_audit",
                OutboxEvent.aggregate_id == order.id,
                OutboxEvent.event_type == "ORDER_ASSET_DOWNLOADED",
            )
        )
        or 0
    )
    attempts = list(
        (
            await db.scalars(
                select(GenerationAttempt)
                .where(GenerationAttempt.job_id == job.id)
                .order_by(GenerationAttempt.attempt_number)
                .with_for_update()
            )
        ).all()
    )
    plan = plan_partner_withdrawal(
        order_status=order.status,
        job_status=job.status,
        settlement_status=order.settlement_status,
        successful_final_downloads=successful_downloads,
        attempt_statuses=[attempt.status for attempt in attempts],
    )
    assets = list(
        (
            await db.scalars(
                select(MediaAsset)
                .where(
                    or_(
                        MediaAsset.id == invite.partner_asset_id,
                        MediaAsset.order_id == order.id,
                        MediaAsset.job_id == job.id,
                    )
                )
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        ).all()
    )
    for asset in assets:
        await _stage_case_asset(
            db,
            asset,
            reason="partner_consent_withdrawn",
            now=current,
        )
    entitlements = list(
        (
            await db.scalars(
                select(OrderEntitlement)
                .where(
                    OrderEntitlement.order_id == order.id,
                    OrderEntitlement.status == EntitlementStatus.ACTIVE.value,
                )
                .with_for_update()
            )
        ).all()
    )
    for entitlement in entitlements:
        entitlement.status = EntitlementStatus.REVOKED
        entitlement.revoked_at = current
        entitlement.revoke_reason = "partner_consent_withdrawn"
    settlement_transaction_id = None
    if plan.release_reserved:
        await release_reservation(
            db,
            reservation_id=order.reservation_id,
            idempotency_key=f"partner-withdraw-release:{invite.id}",
            pre_submission_confirmed=True,
            now=current,
        )
        order.settlement_status = "RELEASED"
        job.settlement_status = "RELEASED"
        job.status = GenerationJobStatus.CANCELLED
        job.finished_at = current
    elif plan.refund_captured:
        settlement = await refund_captured_reservation(
            db,
            reservation_id=order.reservation_id,
            idempotency_key=f"partner-withdraw-refund:{invite.id}",
            reason_code="partner_consent_withdrawn",
            now=current,
        )
        settlement_transaction_id = (
            settlement.transaction.id if settlement.transaction is not None else None
        )
        order.settlement_status = "REFUNDED"
        job.settlement_status = "REFUNDED"
    order.status = OrderStatus.CONSENT_REVIEW_REQUIRED
    order.delivery_status = "REVOKED"
    job.delivery_status = "REVOKED"
    settled = not plan.request_provider_cancel
    case = PartnerConsentCase(
        id=uuid.uuid4(),
        invite_id=invite.id,
        host_user_id=invite.host_user_id,
        partner_user_id=partner_user_id,
        order_id=order.id,
        job_id=job.id,
        status=(
            PartnerConsentCaseStatus.SETTLED_DELETION_PENDING
            if settled
            else PartnerConsentCaseStatus.OPEN
        ),
        reason_code="partner_consent_withdrawn",
        owned_asset_ids=[str(asset.id) for asset in assets],
        provider_cancel_required=plan.request_provider_cancel,
        settlement_transaction_id=settlement_transaction_id,
        settled_at=current if settled else None,
        version=1,
    )
    db.add(case)
    before = _value(invite.status)
    invite.version = int(invite.version) + 1
    _audit_transition(
        db,
        invite=invite,
        actor_user_id=partner_user_id,
        actor_role=PartnerActorRole.PARTNER,
        command=PartnerInviteCommand.WITHDRAW,
        from_status=before,
        request_id=request_id,
        details={"consent_case_id": str(case.id)},
    )
    await db.flush()
    return PartnerWithdrawalRead(
        invite_id=invite.id,
        invite_status=status.value,
        case_id=case.id,
        case_status=_value(case.status),
        order_status=_value(order.status),
    )


async def settle_open_partner_consent_case_after_provider(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    provider_terminal_state: str,
    now: datetime | None = None,
) -> bool:
    """Settle an OPEN withdrawal only after a durable terminal Provider fact."""

    terminal_state = str(provider_terminal_state or "").strip().upper()
    if terminal_state not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        raise PartnerInviteError("partner_provider_state_not_terminal", status_code=409)
    current = _now(now)
    invite = await db.scalar(
        select(PartnerInvite)
        .where(PartnerInvite.job_id == job_id)
        .with_for_update()
    )
    if invite is None:
        return False
    case = await db.scalar(
        select(PartnerConsentCase)
        .where(
            PartnerConsentCase.job_id == job_id,
            PartnerConsentCase.status == PartnerConsentCaseStatus.OPEN.value,
        )
        .with_for_update()
    )
    if case is None:
        return False
    if case.invite_id != invite.id:
        raise PartnerInviteError("partner_withdrawal_lineage_invalid", status_code=409)
    order = await db.scalar(
        select(Order).where(Order.id == case.order_id).with_for_update()
    )
    job = await db.scalar(
        select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
    )
    attempt = await db.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if (
        order is None
        or job is None
        or attempt is None
        or order.generation_job_id != job.id
        or job.order_id != order.id
        or attempt.job_id != job.id
        or order.reservation_id is None
    ):
        raise PartnerInviteError("partner_withdrawal_lineage_invalid", status_code=409)
    reservation = await db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == order.reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise PartnerInviteError("partner_withdrawal_reservation_missing", status_code=409)
    successful_downloads = int(
        await db.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.aggregate_type == "order_download_audit",
                OutboxEvent.aggregate_id == order.id,
                OutboxEvent.event_type == "ORDER_ASSET_DOWNLOADED",
            )
        )
        or 0
    )

    settlement_transaction_id = None
    reservation_status = ReservationStatus(reservation.status)
    if reservation_status is ReservationStatus.RESERVED:
        await release_reservation(
            db,
            reservation_id=reservation.id,
            idempotency_key=f"partner-provider-release:{case.invite_id}",
            pre_submission_confirmed=True,
            now=current,
        )
        settlement_status = "RELEASED"
    elif reservation_status is ReservationStatus.CAPTURED:
        settlement_status = "CAPTURED"
        if successful_downloads == 0:
            settlement = await refund_captured_reservation(
                db,
                reservation_id=reservation.id,
                idempotency_key=f"partner-provider-refund:{case.invite_id}",
                reason_code="partner_consent_withdrawn",
                now=current,
            )
            settlement_transaction_id = (
                settlement.transaction.id if settlement.transaction is not None else None
            )
            settlement_status = "REFUNDED"
    elif reservation_status in {ReservationStatus.RELEASED, ReservationStatus.EXPIRED}:
        settlement_status = "RELEASED"
    else:
        raise PartnerInviteError("partner_withdrawal_reservation_invalid", status_code=409)

    attempt_status = GenerationAttemptStatus(attempt.status)
    if attempt_status not in {GenerationAttemptStatus.FAILED, GenerationAttemptStatus.FINISHED}:
        attempt.status = validate_attempt_transition(
            attempt_status,
            GenerationAttemptStatus.FAILED,
        )
        attempt.finished_at = current
    job_status = GenerationJobStatus(job.status)
    if job_status in {GenerationJobStatus.ACTIVE, GenerationJobStatus.RECONCILING}:
        job.status = validate_job_transition(job_status, GenerationJobStatus.CANCELLED)
    elif job_status is not GenerationJobStatus.CANCELLED:
        raise PartnerInviteError("partner_withdrawal_job_not_settleable", status_code=409)
    job.finished_at = job.finished_at or current
    job.settlement_status = settlement_status
    job.delivery_status = "REVOKED"
    job.lease_owner = None
    job.lease_claim_id = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    order.status = OrderStatus.CONSENT_REVIEW_REQUIRED
    order.settlement_status = settlement_status
    order.delivery_status = "REVOKED"
    case.status = PartnerConsentCaseStatus.SETTLED_DELETION_PENDING
    case.provider_cancel_required = False
    case.settlement_transaction_id = settlement_transaction_id
    case.settled_at = current
    case.version = int(case.version) + 1
    await db.flush()
    return True
