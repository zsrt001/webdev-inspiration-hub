"""Compensation-based commercial lineage transfer for proof-approved merges."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_grant_lot import CreditGrantLot
from app.models.credit_purchase import CreditPurchase, CreditPurchaseStatus
from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.credit_transaction import CreditTransaction, CreditTransactionType
from app.models.asset_access_grant import AssetAccessGrant
from app.models.generation_attempt import GenerationAttempt, GenerationAttemptStatus
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.media_asset import MediaAsset
from app.models.order import Order
from app.models.order_entitlement import EntitlementStatus, OrderEntitlement
from app.models.qa_verdict import QaDecision, QaVerdict
from app.models.payment_reconciliation_case import (
    PaymentReconciliationCase,
    ReconciliationCaseStatus,
)
from app.models.partner_consent_case import (
    PartnerConsentCase,
    PartnerConsentCaseStatus,
)
from app.models.partner_invite import PartnerInvite, PartnerInviteStatus
from app.models.subscription_cancel_intent import CancelIntentState, SubscriptionCancelIntent
from app.models.subscription_credit_grant import SubscriptionCreditGrant
from app.models.subscription_invoice import (
    SubscriptionInvoice,
    SubscriptionInvoiceAdjustmentFact,
)
from app.models.user import User
from app.models.user_credit import UserCredit
from app.models.user_subscription import NormalizedSubscriptionStatus, UserSubscription
from app.services.idempotency_service import lock_idempotency_scope


class AccountMergeCreditError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AccountMergeCreditResult:
    canonical_user_id: uuid.UUID
    legacy_user_id: uuid.UUID
    transferred_balance: int
    transferred_lot_count: int
    replayed: bool


def ordered_merge_user_ids(
    first: uuid.UUID,
    second: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    return tuple(sorted((first, second), key=lambda item: item.int))  # type: ignore[return-value]


def _status(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def require_generation_graph_mergeable(
    *,
    jobs: list,
    attempts: list,
    reservations: list,
    qa_verdicts: list | None = None,
    grants: list | None = None,
) -> None:
    """Reject every generation graph that is nonterminal or not fully settled."""

    if any(
        _status(job.status)
        in {
            GenerationJobStatus.QUEUED.value,
            GenerationJobStatus.ACTIVE.value,
            GenerationJobStatus.RECONCILING.value,
        }
        for job in jobs
    ):
        raise AccountMergeCreditError("nonterminal_generation_job")
    if any(
        getattr(job, "lease_owner", None) is not None
        or getattr(job, "lease_claim_id", None) is not None
        or getattr(job, "lease_expires_at", None) is not None
        for job in jobs
    ):
        raise AccountMergeCreditError("generation_lease_unsettled")
    if any(
        _status(attempt.status)
        in {
            GenerationAttemptStatus.PREPARED.value,
            GenerationAttemptStatus.SUBMITTING.value,
            GenerationAttemptStatus.SUBMITTED.value,
            GenerationAttemptStatus.UNKNOWN.value,
        }
        for attempt in attempts
    ):
        raise AccountMergeCreditError("nonterminal_generation_attempt")
    if any(_status(item.status) == ReservationStatus.RESERVED.value for item in reservations):
        raise AccountMergeCreditError("nonterminal_reservation_exists")
    if any(
        _status(item.status) == ReservationStatus.CAPTURED.value
        and getattr(item, "provider_attempt_id", None) is None
        for item in reservations
    ):
        raise AccountMergeCreditError("generation_settlement_incomplete")

    verdicts_by_job: dict[uuid.UUID, set[str]] = {}
    for verdict in qa_verdicts or []:
        verdicts_by_job.setdefault(verdict.job_id, set()).add(_status(verdict.decision))
    for job in jobs:
        status = _status(job.status)
        settlement = str(getattr(job, "settlement_status", "") or "").upper()
        delivery = str(getattr(job, "delivery_status", "") or "").upper()
        if status == GenerationJobStatus.FINISHED.value:
            captured_delivery = settlement == "CAPTURED" and delivery in {
                "PUBLISHED", "READY", "REVOKED", "DELETED"
            }
            withdrawn_delivery = settlement == "REFUNDED" and delivery in {
                "REVOKED", "DELETED"
            }
            if not captured_delivery and not withdrawn_delivery:
                raise AccountMergeCreditError("generation_settlement_incomplete")
            if (
                settlement == "CAPTURED"
                and qa_verdicts is not None
                and QaDecision.PASS.value not in verdicts_by_job.get(job.id, set())
            ):
                raise AccountMergeCreditError("generation_qa_publication_incomplete")
        elif status in {
            GenerationJobStatus.FAILED.value,
            GenerationJobStatus.CANCELLED.value,
        }:
            if settlement not in {"RELEASED", "REFUNDED"} or delivery not in {
                "NOT_DELIVERED",
                "REVOKED",
                "DELETED",
            }:
                raise AccountMergeCreditError("generation_settlement_incomplete")
        else:
            raise AccountMergeCreditError("generation_status_unknown")
    if grants is not None and any(getattr(item, "revoked_at", None) is None for item in grants):
        raise AccountMergeCreditError("generation_grant_unsettled")


def rebind_terminal_partner_references(
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    invites: list,
    consent_cases: list,
) -> None:
    """Rebind only closed Partner lineage without collapsing both participants."""

    if any(
        _status(case.status)
        in {
            PartnerConsentCaseStatus.OPEN.value,
            PartnerConsentCaseStatus.SETTLED_DELETION_PENDING.value,
        }
        for case in consent_cases
    ):
        raise AccountMergeCreditError("partner_consent_case_nonterminal")
    terminal_invite_statuses = {
        PartnerInviteStatus.COMPLETED.value,
        PartnerInviteStatus.REVOKED.value,
        PartnerInviteStatus.EXPIRED.value,
        PartnerInviteStatus.CANCELLED.value,
    }
    if any(_status(invite.status) not in terminal_invite_statuses for invite in invites):
        raise AccountMergeCreditError("partner_invite_nonterminal")
    if any(
        {invite.host_user_id, invite.partner_user_id}
        == {canonical_user_id, legacy_user_id}
        for invite in invites
        if invite.partner_user_id is not None
    ):
        raise AccountMergeCreditError("partner_invite_identity_collision")

    for invite in invites:
        changed = False
        if invite.host_user_id == legacy_user_id:
            invite.host_user_id = canonical_user_id
            changed = True
        if invite.partner_user_id == legacy_user_id:
            invite.partner_user_id = canonical_user_id
            changed = True
        if changed:
            invite.version = int(invite.version) + 1
    for case in consent_cases:
        changed = False
        if case.host_user_id == legacy_user_id:
            case.host_user_id = canonical_user_id
            changed = True
        if case.partner_user_id == legacy_user_id:
            case.partner_user_id = canonical_user_id
            changed = True
        if changed:
            case.version = int(case.version) + 1


def select_subscription_projection_for_merge(
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    subscriptions: list[UserSubscription],
    invoices: list[SubscriptionInvoice],
    grants: list[SubscriptionCreditGrant],
    cancel_intents: list[SubscriptionCancelIntent],
) -> UserSubscription | None:
    """Validate normalized immutable lineage and choose one mutable projection.

    Invoice and grant ownership is historical evidence and is never rewritten.
    Only a single legacy ACTIVE/PAST_DUE projection may move to the canonical
    user after every pending monetary/cancellation state has been excluded.
    """

    known_statuses = {item.value for item in NormalizedSubscriptionStatus}
    provider_ids: set[tuple[str, str]] = set()
    by_subscription_id = {item.id: item for item in subscriptions}
    invoice_by_id = {item.id: item for item in invoices}
    for subscription in subscriptions:
        status = _status(subscription.normalized_status)
        if status not in known_statuses:
            raise AccountMergeCreditError("subscription_lineage_not_normalized")
        if (
            not subscription.provider_subscription_id
            or not subscription.product_code
            or subscription.catalog_version_id is None
            or not isinstance(subscription.catalog_snapshot, dict)
            or not subscription.catalog_snapshot
        ):
            raise AccountMergeCreditError("subscription_lineage_not_normalized")
        provider_key = (
            str(subscription.provider or "").strip().lower(),
            str(subscription.provider_subscription_id).strip(),
        )
        if provider_key in provider_ids:
            raise AccountMergeCreditError("subscription_provider_id_conflict")
        provider_ids.add(provider_key)
    for invoice in invoices:
        if invoice.subscription_id not in by_subscription_id:
            raise AccountMergeCreditError("subscription_lineage_not_normalized")
        refunded = int(invoice.refunded_minor_units or 0)
        if 0 < refunded < invoice.total_minor_units:
            raise AccountMergeCreditError("subscription_refund_or_dispute_pending")
        if str(invoice.dispute_state or "NONE").upper() in {
            "OPEN",
            "REVIEW",
            "PARTIAL",
        }:
            raise AccountMergeCreditError("subscription_refund_or_dispute_pending")
    for grant in grants:
        invoice = invoice_by_id.get(grant.invoice_id)
        if (
            invoice is None
            or invoice.subscription_id != grant.subscription_id
            or grant.grant_lot_id is None
            or grant.period_start is None
            or grant.period_end is None
        ):
            raise AccountMergeCreditError("subscription_lineage_not_normalized")
    for intent in cancel_intents:
        if intent.subscription_id not in by_subscription_id:
            raise AccountMergeCreditError("subscription_lineage_not_normalized")
        if _status(intent.state) in {
            CancelIntentState.NEW.value,
            CancelIntentState.CALLING.value,
            CancelIntentState.UNKNOWN.value,
            CancelIntentState.FAILED_RETRYABLE.value,
        }:
            raise AccountMergeCreditError("subscription_cancellation_pending")

    nonterminal = [
        item
        for item in subscriptions
        if _status(item.normalized_status)
        in {
            NormalizedSubscriptionStatus.PENDING.value,
            NormalizedSubscriptionStatus.ACTIVE.value,
            NormalizedSubscriptionStatus.PAST_DUE.value,
            NormalizedSubscriptionStatus.CANCEL_REQUESTED.value,
        }
    ]
    if any(
        _status(item.normalized_status)
        in {
            NormalizedSubscriptionStatus.PENDING.value,
            NormalizedSubscriptionStatus.CANCEL_REQUESTED.value,
        }
        for item in nonterminal
    ):
        raise AccountMergeCreditError("subscription_operation_pending")
    if len(nonterminal) > 1:
        raise AccountMergeCreditError("dual_open_subscription")
    if not nonterminal or nonterminal[0].user_id == canonical_user_id:
        return None
    projection = nonterminal[0]
    if projection.user_id != legacy_user_id:
        raise AccountMergeCreditError("subscription_projection_owner_conflict")
    return projection


async def _locked_list(db: AsyncSession, statement) -> list:
    return list((await db.scalars(statement.with_for_update())).all())


async def merge_credit_accounts(
    db: AsyncSession,
    *,
    canonical_user_id: uuid.UUID,
    legacy_user_id: uuid.UUID,
    request_id: str,
) -> AccountMergeCreditResult:
    if canonical_user_id == legacy_user_id:
        raise AccountMergeCreditError("self_merge_forbidden")
    clean_request_id = str(request_id or "").strip()
    if not clean_request_id or len(clean_request_id) > 96:
        raise AccountMergeCreditError("merge_request_id_invalid")
    await lock_idempotency_scope(
        db,
        user_id=canonical_user_id,
        endpoint="account.merge.credit",
        key=clean_request_id,
    )
    replay_transaction = await db.scalar(
        select(CreditTransaction).where(
            CreditTransaction.user_id == canonical_user_id,
            CreditTransaction.transaction_type == CreditTransactionType.ACCOUNT_MERGE_IN,
            CreditTransaction.request_id == f"{clean_request_id}:in",
        )
    )
    if replay_transaction is not None:
        return AccountMergeCreditResult(
            canonical_user_id=canonical_user_id,
            legacy_user_id=legacy_user_id,
            transferred_balance=int(replay_transaction.amount),
            transferred_lot_count=0,
            replayed=True,
        )

    ordered_ids = ordered_merge_user_ids(canonical_user_id, legacy_user_id)
    users = await _locked_list(
        db,
        select(User).where(User.id.in_(ordered_ids)).order_by(User.id),
    )
    if {item.id for item in users} != set(ordered_ids):
        raise AccountMergeCreditError("merge_user_missing")

    partner_invites = await _locked_list(
        db,
        select(PartnerInvite)
        .where(
            or_(
                PartnerInvite.host_user_id.in_(ordered_ids),
                PartnerInvite.partner_user_id.in_(ordered_ids),
            )
        )
        .order_by(PartnerInvite.id),
    )
    partner_consent_cases = await _locked_list(
        db,
        select(PartnerConsentCase)
        .where(
            or_(
                PartnerConsentCase.host_user_id.in_(ordered_ids),
                PartnerConsentCase.partner_user_id.in_(ordered_ids),
            )
        )
        .order_by(PartnerConsentCase.id),
    )
    rebind_terminal_partner_references(
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        invites=partner_invites,
        consent_cases=partner_consent_cases,
    )

    generation_jobs = await _locked_list(
        db,
        select(GenerationJob)
        .join(Order, Order.id == GenerationJob.order_id)
        .where(Order.user_id.in_(ordered_ids))
        .order_by(GenerationJob.id),
    )
    generation_job_ids = [item.id for item in generation_jobs]
    generation_attempts = (
        await _locked_list(
            db,
            select(GenerationAttempt)
            .where(GenerationAttempt.job_id.in_(generation_job_ids))
            .order_by(GenerationAttempt.job_id, GenerationAttempt.attempt_number),
        )
        if generation_job_ids
        else []
    )
    qa_verdicts = (
        await _locked_list(
            db,
            select(QaVerdict)
            .where(QaVerdict.job_id.in_(generation_job_ids))
            .order_by(QaVerdict.job_id, QaVerdict.id),
        )
        if generation_job_ids
        else []
    )
    generation_grants = (
        await _locked_list(
            db,
            select(AssetAccessGrant)
            .where(AssetAccessGrant.job_id.in_(generation_job_ids))
            .order_by(AssetAccessGrant.job_id, AssetAccessGrant.id),
        )
        if generation_job_ids
        else []
    )

    reservations = await _locked_list(
        db,
        select(CreditReservation)
        .where(CreditReservation.user_id.in_(ordered_ids))
        .order_by(CreditReservation.user_id, CreditReservation.id),
    )
    require_generation_graph_mergeable(
        jobs=generation_jobs,
        attempts=generation_attempts,
        reservations=reservations,
        qa_verdicts=qa_verdicts,
        grants=generation_grants,
    )
    subscriptions = await _locked_list(
        db,
        select(UserSubscription)
        .where(UserSubscription.user_id.in_(ordered_ids))
        .order_by(UserSubscription.user_id, UserSubscription.id),
    )
    subscription_grants = await _locked_list(
        db,
        select(SubscriptionCreditGrant)
        .where(SubscriptionCreditGrant.user_id.in_(ordered_ids))
        .order_by(SubscriptionCreditGrant.user_id, SubscriptionCreditGrant.id),
    )
    subscription_ids = [item.id for item in subscriptions]
    invoices = (
        await _locked_list(
            db,
            select(SubscriptionInvoice)
            .where(SubscriptionInvoice.subscription_id.in_(subscription_ids))
            .order_by(SubscriptionInvoice.subscription_id, SubscriptionInvoice.id),
        )
        if subscription_ids
        else []
    )
    invoice_ids = [item.id for item in invoices]
    if invoice_ids:
        await _locked_list(
            db,
            select(SubscriptionInvoiceAdjustmentFact)
            .where(SubscriptionInvoiceAdjustmentFact.invoice_id.in_(invoice_ids))
            .order_by(
                SubscriptionInvoiceAdjustmentFact.invoice_id,
                SubscriptionInvoiceAdjustmentFact.id,
            ),
        )
    cancel_intents = (
        await _locked_list(
            db,
            select(SubscriptionCancelIntent)
            .where(SubscriptionCancelIntent.subscription_id.in_(subscription_ids))
            .order_by(
                SubscriptionCancelIntent.subscription_id,
                SubscriptionCancelIntent.id,
            ),
        )
        if subscription_ids
        else []
    )
    subscription_projection = select_subscription_projection_for_merge(
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        subscriptions=subscriptions,
        invoices=invoices,
        grants=subscription_grants,
        cancel_intents=cancel_intents,
    )
    holds = await _locked_list(
        db,
        select(CreditPurchase)
        .where(
            CreditPurchase.user_id.in_(ordered_ids),
            CreditPurchase.status == CreditPurchaseStatus.PENDING.value,
        )
        .order_by(CreditPurchase.user_id, CreditPurchase.id),
    )
    commercial_subject_ids = [
        *(str(item.id) for item in subscriptions),
        *(str(item.id) for item in invoices),
    ]
    open_case_owner_or_subject = PaymentReconciliationCase.user_id.in_(ordered_ids)
    if commercial_subject_ids:
        open_case_owner_or_subject = or_(
            open_case_owner_or_subject,
            PaymentReconciliationCase.subject_id.in_(commercial_subject_ids),
        )
    open_cases = await _locked_list(
        db,
        select(PaymentReconciliationCase)
        .where(
            open_case_owner_or_subject,
            PaymentReconciliationCase.status.in_(
                [
                    ReconciliationCaseStatus.OPEN.value,
                    ReconciliationCaseStatus.ESCALATED.value,
                ]
            ),
        )
        .order_by(PaymentReconciliationCase.user_id, PaymentReconciliationCase.id),
    )
    if holds or open_cases:
        raise AccountMergeCreditError("payment_hold_or_reconciliation_open")

    credits = await _locked_list(
        db,
        select(UserCredit)
        .where(UserCredit.user_id.in_(ordered_ids))
        .order_by(UserCredit.user_id),
    )
    credit_by_user = {item.user_id: item for item in credits}
    canonical_credit = credit_by_user.get(canonical_user_id)
    legacy_credit = credit_by_user.get(legacy_user_id)
    if canonical_credit is None:
        canonical_credit = UserCredit(
            id=uuid.uuid4(),
            user_id=canonical_user_id,
            balance=0,
            reserved_balance=0,
        )
        db.add(canonical_credit)
        await db.flush()
    if legacy_credit is None:
        legacy_credit = UserCredit(
            id=uuid.uuid4(),
            user_id=legacy_user_id,
            balance=0,
            reserved_balance=0,
        )
        db.add(legacy_credit)
        await db.flush()
    if int(canonical_credit.reserved_balance or 0) or int(legacy_credit.reserved_balance or 0):
        raise AccountMergeCreditError("reserved_balance_nonzero")

    lots = await _locked_list(
        db,
        select(CreditGrantLot)
        .where(CreditGrantLot.user_id == legacy_user_id)
        .order_by(CreditGrantLot.id),
    )
    transferred_balance = int(legacy_credit.balance or 0)
    if transferred_balance > 0 and not lots:
        raise AccountMergeCreditError("legacy_pool_migration_required")
    canonical_next_balance = int(canonical_credit.balance or 0) + transferred_balance
    if transferred_balance:
        out_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=legacy_user_id,
            transaction_type=CreditTransactionType.ACCOUNT_MERGE_OUT,
            amount=-transferred_balance,
            balance_after=0,
            source="account_merge",
            source_id=str(canonical_user_id),
            request_id=f"{clean_request_id}:out",
            metadata_json={
                "canonical_user_id": str(canonical_user_id),
                "legacy_user_id": str(legacy_user_id),
            },
        )
        in_transaction = CreditTransaction(
            id=uuid.uuid4(),
            user_id=canonical_user_id,
            transaction_type=CreditTransactionType.ACCOUNT_MERGE_IN,
            amount=transferred_balance,
            balance_after=canonical_next_balance,
            source="account_merge",
            source_id=str(legacy_user_id),
            request_id=f"{clean_request_id}:in",
            metadata_json={
                "canonical_user_id": str(canonical_user_id),
                "legacy_user_id": str(legacy_user_id),
                "paired_out_transaction_id": str(out_transaction.id),
            },
        )
        out_transaction.metadata_json["paired_in_transaction_id"] = str(in_transaction.id)
        db.add(out_transaction)
        db.add(in_transaction)
    canonical_credit.balance = canonical_next_balance
    legacy_credit.balance = 0
    for lot in lots:
        lot.user_id = canonical_user_id

    if subscription_projection is not None:
        subscription_projection.user_id = canonical_user_id

    for reservation in reservations:
        if reservation.user_id == legacy_user_id:
            reservation.user_id = canonical_user_id
    mutable_rows: list = []
    for model, user_column in (
        (Order, Order.user_id),
        (MediaAsset, MediaAsset.owner_user_id),
    ):
        mutable_rows.extend(
            await _locked_list(
                db,
                select(model).where(user_column == legacy_user_id).order_by(model.id),
            )
        )
    for row in mutable_rows:
        if isinstance(row, MediaAsset):
            row.owner_user_id = canonical_user_id
        else:
            row.user_id = canonical_user_id
    entitlements = await _locked_list(
        db,
        select(OrderEntitlement)
        .where(OrderEntitlement.user_id.in_(ordered_ids))
        .order_by(OrderEntitlement.user_id, OrderEntitlement.id),
    )
    for entitlement in entitlements:
        if (
            entitlement.user_id == legacy_user_id
            and _status(entitlement.status) == EntitlementStatus.ACTIVE.value
        ):
            entitlement.user_id = canonical_user_id
    await db.flush()
    return AccountMergeCreditResult(
        canonical_user_id=canonical_user_id,
        legacy_user_id=legacy_user_id,
        transferred_balance=transferred_balance,
        transferred_lot_count=len(lots),
        replayed=False,
    )
