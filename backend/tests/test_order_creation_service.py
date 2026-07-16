"""Order admission tests for the private-asset durable-job contract."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.order import OrderCreate
from app.services import order_creation_service as service


NOW = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)


class OrderCreationServiceTest(unittest.TestCase):
    def _request(self, *, asset_count: int = 1, **overrides) -> OrderCreate:
        values = {
            "template_id": "solo_royal_castle" if asset_count == 1 else "royal_castle",
            "asset_ids": [uuid.uuid4() for _ in range(asset_count)],
            "legal_accepted": True,
        }
        values.update(overrides)
        return OrderCreate(**values)

    def _facts(self, *, welcome_credits: int = 2, **overrides) -> service.OrderAdmissionFacts:
        values = {
            "catalog_version_id": uuid.uuid4(),
            "catalog_version": "2026-07-10",
            "catalog_release_sha": "a" * 40,
            "welcome_claim_id": uuid.uuid4(),
            "welcome_spendable_credits": welcome_credits,
            "trial_attempts_in_rolling_24h": 0,
            "ready_trial_exists": False,
        }
        values.update(overrides)
        return service.OrderAdmissionFacts(**values)

    def test_external_url_identity_contract_is_rejected_by_schema(self) -> None:
        with self.assertRaises(ValidationError):
            OrderCreate(
                template_id="solo_royal_castle",
                user_images=["https://example.invalid/person.jpg"],
                legal_accepted=True,
            )

    def test_canonical_hash_is_stable_and_sensitive_to_asset_order(self) -> None:
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        first = self._request(asset_count=2, asset_ids=[first_id, second_id])
        replay = self._request(asset_count=2, asset_ids=[first_id, second_id])
        reversed_request = self._request(asset_count=2, asset_ids=[second_id, first_id])

        self.assertEqual(service.canonical_order_request_hash(first), service.canonical_order_request_hash(replay))
        self.assertNotEqual(
            service.canonical_order_request_hash(first),
            service.canonical_order_request_hash(reversed_request),
        )

    def test_base_single_welcome_order_builds_exact_trial_policy(self) -> None:
        user_id = uuid.uuid4()
        request = self._request()
        facts = self._facts()
        command = service.build_create_order_command(
            request=request,
            user_id=user_id,
            idempotency_key="order-once",
            facts=facts,
        )

        self.assertEqual(command.user_id, user_id)
        self.assertEqual(command.asset_ids, tuple(request.asset_ids))
        self.assertEqual(command.credit_cost, 2)
        self.assertEqual(command.product_policy.product_code, "generation_single_base")
        self.assertEqual(command.product_policy.credit_cost, 2)
        self.assertTrue(command.funding_policy.is_trial)
        self.assertEqual(command.funding_policy.allowed_lot_class, "WELCOME_ONLY")
        self.assertEqual(command.funding_policy.identity_claim_id, facts.welcome_claim_id)

    def test_paid_couple_never_allocates_welcome_lots(self) -> None:
        request = self._request(asset_count=2)
        command = service.build_create_order_command(
            request=request,
            user_id=uuid.uuid4(),
            idempotency_key="couple-once",
            facts=self._facts(),
        )

        self.assertEqual(command.credit_cost, 3)
        self.assertEqual(command.product_policy.product_code, "generation_couple_base")
        self.assertFalse(command.funding_policy.is_trial)
        self.assertEqual(command.funding_policy.allowed_lot_class, "PAID_ONLY")

    def test_director_and_raw_reference_urls_cannot_consume_welcome_trial(self) -> None:
        director = self._request(director_mode=True, global_style_text="quiet editorial portrait")
        command = service.build_create_order_command(
            request=director,
            user_id=uuid.uuid4(),
            idempotency_key="director-once",
            facts=self._facts(),
        )
        self.assertFalse(command.funding_policy.is_trial)
        self.assertEqual(command.product_policy.product_code, "generation_single_director")

        raw_reference = self._request(scene_image_url="https://example.invalid/scene.jpg")
        with self.assertRaises(HTTPException) as raised:
            service.build_create_order_command(
                request=raw_reference,
                user_id=uuid.uuid4(),
                idempotency_key="raw-url",
                facts=self._facts(),
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["code"], "legacy_reference_url_forbidden")

    def test_trial_attempt_and_ready_limits_fail_closed_to_paid_policy(self) -> None:
        request = self._request()
        for facts in (
            self._facts(trial_attempts_in_rolling_24h=3),
            self._facts(ready_trial_exists=True),
            self._facts(welcome_credits=1),
        ):
            with self.subTest(facts=facts):
                command = service.build_create_order_command(
                    request=request,
                    user_id=uuid.uuid4(),
                    idempotency_key=str(uuid.uuid4()),
                    facts=facts,
                )
                self.assertFalse(command.funding_policy.is_trial)
                self.assertEqual(command.funding_policy.allowed_lot_class, "PAID_ONLY")

    def test_legal_template_subject_and_prompt_checks_precede_transaction(self) -> None:
        cases = (
            (self._request(legal_accepted=False), "legal_acceptance_required"),
            (self._request(template_id="missing-template"), "template_not_available"),
            (
                self._request(asset_count=2, template_id="solo_royal_castle"),
                "template_subject_count_mismatch",
            ),
            (self._request(global_style_text="explicit nude portrait"), "explicit_prompt_disallowed"),
        )
        for request, code in cases:
            with self.subTest(code=code), self.assertRaises(HTTPException) as raised:
                service.build_create_order_command(
                    request=request,
                    user_id=uuid.uuid4(),
                    idempotency_key=str(uuid.uuid4()),
                    facts=self._facts(),
                )
            self.assertEqual(raised.exception.detail["code"], code)

    def test_idempotency_key_is_required_and_bounded(self) -> None:
        for key in ("", "x" * 129):
            with self.subTest(key_length=len(key)), self.assertRaises(HTTPException) as raised:
                service.build_create_order_command(
                    request=self._request(),
                    user_id=uuid.uuid4(),
                    idempotency_key=key,
                    facts=self._facts(),
                )
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(raised.exception.detail["code"], "idempotency_key_invalid")


class OrderCreationOrchestrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_gatekeeper_precedes_atomic_transaction_and_commit(self) -> None:
        request = OrderCreate(
            template_id="solo_royal_castle",
            asset_ids=[uuid.uuid4()],
            legal_accepted=True,
        )
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()
        db.scalar.return_value = None
        facts = service.OrderAdmissionFacts(
            catalog_version_id=uuid.uuid4(),
            catalog_version="2026-07-10",
            catalog_release_sha="a" * 40,
            welcome_claim_id=uuid.uuid4(),
            welcome_spendable_credits=2,
            trial_attempts_in_rolling_24h=0,
            ready_trial_exists=False,
        )
        accepted = service.AcceptedOrder(
            order_id=uuid.uuid4(),
            status="QUEUED",
            status_url="/api/v1/orders/example",
        )
        events: list[str] = []

        async def gate(*_args, **_kwargs):
            events.append("gatekeeper")

        async def transact(*_args, **_kwargs):
            events.append("transaction")
            return accepted

        with (
            patch.object(service, "require_server_runtime_execution_stamp"),
            patch.object(service, "_load_admission_facts", AsyncMock(return_value=facts)),
            patch.object(service, "_run_gatekeeper_checks", new=gate),
            patch.object(service, "create_order_transaction", new=transact),
        ):
            result = await service.create_order_for_user(
                request,
                user,
                db,
                idempotency_key="order-once",
            )

        self.assertEqual(result, accepted)
        self.assertEqual(events, ["gatekeeper", "transaction"])
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

    async def test_insufficient_credit_rolls_back_atomic_rows_and_returns_402(self) -> None:
        request = OrderCreate(
            template_id="solo_royal_castle",
            asset_ids=[uuid.uuid4()],
            legal_accepted=True,
        )
        user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()
        db.scalar.return_value = None
        facts = service.OrderAdmissionFacts(
            catalog_version_id=uuid.uuid4(),
            catalog_version="2026-07-10",
            catalog_release_sha="a" * 40,
            welcome_claim_id=None,
            welcome_spendable_credits=0,
            trial_attempts_in_rolling_24h=0,
            ready_trial_exists=False,
        )
        with (
            patch.object(service, "require_server_runtime_execution_stamp"),
            patch.object(service, "_load_admission_facts", AsyncMock(return_value=facts)),
            patch.object(service, "_run_gatekeeper_checks", AsyncMock()),
            patch.object(
                service,
                "create_order_transaction",
                AsyncMock(side_effect=service.InsufficientCredits(2)),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            await service.create_order_for_user(
                request,
                user,
                db,
                idempotency_key="order-insufficient",
            )

        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(raised.exception.detail["code"], "insufficient_credits")
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
