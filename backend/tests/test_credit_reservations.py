"""Deterministic credit reservation policy and allocation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import uuid

from app.models.credit_grant_lot import GrantLotSourceType
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.credit_reservation import CreditReservation
from app.services.credit_reservation_service import (
    CreditInvariantViolation,
    FundingPolicyViolation,
    InsufficientCredits,
    OrderFundingPolicySnapshot,
    build_fefo_allocations,
    captured_retention_tier,
    _lock_capture_attempt_and_job,
    validate_capture_attempt_provenance,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _lot(
    lot_id: uuid.UUID,
    *,
    amount: int,
    source_type: GrantLotSourceType,
    expires_at: datetime | None,
    created_at: datetime,
    retention_tier: str,
):
    return SimpleNamespace(
        id=lot_id,
        original_amount=amount,
        debt_offset_amount=0,
        reversed_amount=0,
        frozen_amount=0,
        consumed_amount=0,
        spendable_amount=amount,
        source_type=source_type,
        expires_at=expires_at,
        created_at=created_at,
        retention_tier=retention_tier,
    )


class CreditReservationPolicyTest(unittest.TestCase):
    def _trial_policy(self, **overrides) -> OrderFundingPolicySnapshot:
        values = {
            "generation_mode": "single",
            "subject_count": 1,
            "is_trial": True,
            "identity_claim_id": uuid.uuid4(),
            "attempts_in_rolling_24h": 0,
            "ready_trial_exists": False,
            "allowed_lot_class": "WELCOME_ONLY",
            "scene_tier": "base",
            "director_mode": False,
        }
        values.update(overrides)
        return OrderFundingPolicySnapshot(**values)

    def test_policy_hash_is_canonical_and_trial_contract_is_exact(self) -> None:
        claim_id = uuid.uuid4()
        first = self._trial_policy(identity_claim_id=claim_id)
        second = self._trial_policy(identity_claim_id=claim_id)
        self.assertEqual(first.canonical_hash(), second.canonical_hash())
        first.validate_for_amount(2)

        invalid_overrides = (
            {"subject_count": 2},
            {"generation_mode": "partner_invite"},
            {"generation_mode": "golden_anniversary"},
            {"director_mode": True},
            {"scene_tier": "premium"},
            {"attempts_in_rolling_24h": 3},
            {"ready_trial_exists": True},
        )
        for override in invalid_overrides:
            with self.subTest(override=override), self.assertRaises(FundingPolicyViolation):
                self._trial_policy(**override).validate_for_amount(2)
        with self.assertRaises(FundingPolicyViolation):
            first.validate_for_amount(3)
    def test_paid_policy_never_allocates_welcome_lots(self) -> None:
        policy = OrderFundingPolicySnapshot(
            generation_mode="single",
            subject_count=1,
            is_trial=False,
            identity_claim_id=None,
            attempts_in_rolling_24h=0,
            ready_trial_exists=False,
            allowed_lot_class="PAID_ONLY",
            scene_tier="base",
            director_mode=False,
        )
        policy.validate_for_amount(2)
        self.assertNotIn(GrantLotSourceType.WELCOME, policy.allowed_source_types())

    def test_fefo_allocation_is_stable_and_accounts_for_existing_reservations(self) -> None:
        early_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
        same_time_lower_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
        no_expiry_id = uuid.UUID("00000000-0000-4000-8000-000000000003")
        lots = [
            _lot(
                no_expiry_id,
                amount=8,
                source_type=GrantLotSourceType.PURCHASE,
                expires_at=None,
                created_at=NOW,
                retention_tier="paid_90d",
            ),
            _lot(
                early_id,
                amount=4,
                source_type=GrantLotSourceType.PURCHASE,
                expires_at=NOW + timedelta(days=2),
                created_at=NOW,
                retention_tier="paid_90d",
            ),
            _lot(
                same_time_lower_id,
                amount=4,
                source_type=GrantLotSourceType.SUBSCRIPTION,
                expires_at=NOW + timedelta(days=2),
                created_at=NOW,
                retention_tier="subscription_180d",
            ),
        ]
        allocations = build_fefo_allocations(
            lots,
            amount=7,
            reserved_by_lot={same_time_lower_id: 1},
            now=NOW,
        )
        self.assertEqual(
            [(item.grant_lot_id, item.amount) for item in allocations],
            [(same_time_lower_id, 3), (early_id, 4)],
        )
        self.assertEqual(captured_retention_tier(lots[:2]), "paid_90d")
        self.assertEqual(captured_retention_tier(lots), "subscription_180d")

    def test_fefo_refuses_unfunded_amount(self) -> None:
        lot = _lot(
            uuid.uuid4(),
            amount=2,
            source_type=GrantLotSourceType.PURCHASE,
            expires_at=NOW + timedelta(days=1),
            created_at=NOW,
            retention_tier="paid_90d",
        )
        with self.assertRaises(InsufficientCredits):
            build_fefo_allocations([lot], amount=3, reserved_by_lot={}, now=NOW)

    def test_capture_accepts_only_own_submitted_initial_attempt(self) -> None:
        order_id = uuid.uuid4()
        job = GenerationJob(
            id=uuid.uuid4(),
            order_id=order_id,
            submission_correlation_id=uuid.uuid4(),
            status=GenerationJobStatus.ACTIVE,
            api_deployment_id="deployment",
            runtime_bundle_id="a" * 64,
            expected_worker_image_digest="sha256:" + "b" * 64,
        )
        reservation = CreditReservation(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            order_id=order_id,
            amount=2,
            idempotency_key="reserve",
            request_hash="c" * 64,
            funding_policy_snapshot={},
            funding_policy_hash="d" * 64,
            expires_at=NOW + timedelta(minutes=10),
        )
        attempt = GenerationAttempt(
            id=uuid.uuid4(),
            job_id=job.id,
            attempt_number=1,
            kind=GenerationAttemptKind.INITIAL,
            status=GenerationAttemptStatus.SUBMITTED,
            provider="evolink",
            client_request_id=str(job.submission_correlation_id),
        )

        validate_capture_attempt_provenance(reservation, attempt, job)

        invalid = (
            (None, job, "capture_attempt_not_found"),
            (
                GenerationAttempt(
                    id=uuid.uuid4(), job_id=job.id, attempt_number=2,
                    kind=GenerationAttemptKind.REPAIR,
                    status=GenerationAttemptStatus.SUBMITTED, provider="evolink",
                    client_request_id=str(uuid.uuid4()),
                ),
                job,
                "capture_attempt_not_initial",
            ),
            (
                GenerationAttempt(
                    id=uuid.uuid4(), job_id=uuid.uuid4(), attempt_number=1,
                    kind=GenerationAttemptKind.INITIAL,
                    status=GenerationAttemptStatus.SUBMITTED, provider="evolink",
                    client_request_id=str(uuid.uuid4()),
                ),
                job,
                "capture_attempt_job_mismatch",
            ),
            (
                GenerationAttempt(
                    id=uuid.uuid4(), job_id=job.id, attempt_number=1,
                    kind=GenerationAttemptKind.INITIAL,
                    status=GenerationAttemptStatus.PREPARED, provider="evolink",
                    client_request_id=str(job.submission_correlation_id),
                ),
                job,
                "capture_attempt_not_submitted",
            ),
        )
        for candidate, candidate_job, code in invalid:
            with self.subTest(code=code), self.assertRaises(CreditInvariantViolation) as raised:
                validate_capture_attempt_provenance(reservation, candidate, candidate_job)
            self.assertEqual(raised.exception.code, code)


class CreditCaptureLockOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_capture_locks_job_before_attempt(self) -> None:
        job = GenerationJob.queued(
            order_id=uuid.uuid4(),
            submission_correlation_id=uuid.uuid4(),
            api_deployment_id="dpl_api",
            runtime_bundle_id="rtb_" + "a" * 64,
            expected_worker_image_digest="sha256:" + "b" * 64,
        )
        attempt = GenerationAttempt.prepared(
            job=job,
            attempt_number=1,
            kind=GenerationAttemptKind.INITIAL,
            provider="evolink",
        )
        locked_entities: list[type] = []

        class Db:
            async def scalar(self, statement):
                entity = statement.column_descriptions[0]["entity"]
                if getattr(statement, "_for_update_arg", None) is not None:
                    locked_entities.append(entity)
                return job if entity is GenerationJob else attempt

        locked_attempt, locked_job = await _lock_capture_attempt_and_job(Db(), attempt.id)

        self.assertIs(locked_attempt, attempt)
        self.assertIs(locked_job, job)
        self.assertEqual(locked_entities, [GenerationJob, GenerationAttempt])


if __name__ == "__main__":
    unittest.main()
