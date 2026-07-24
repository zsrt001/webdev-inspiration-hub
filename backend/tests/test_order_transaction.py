"""Atomic order, reservation, and durable generation-job contract."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.idempotency_record import IdempotencyState
from app.models.media_asset import MediaAsset, MediaAssetRole, MediaAssetStatus
from app.models.generation_job import GenerationJob
from app.services.credit_reservation_service import OrderFundingPolicySnapshot
from app.services.idempotency_service import IdempotencyAttempt
from app.services.order_transaction_service import (
    CreateOrderCommand,
    OrderPolicySnapshot,
    RuntimeExecutionStamp,
    create_order_transaction,
)
from app.services.partner_invite_service import create_partner_order
from app.models.partner_invite import PartnerInviteStatus
from app.models.partner_invite_event import PartnerInviteEvent


NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _OrderDb:
    def __init__(self, assets):
        self.assets = assets
        self.added = []

    async def scalars(self, statement):
        if "media_assets" in str(statement):
            return _Rows(self.assets)
        return _Rows([])

    async def scalar(self, statement):
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def _command(user_id, asset_ids):
    return CreateOrderCommand(
        user_id=user_id,
        idempotency_key="order-create-1",
        request_hash="a" * 64,
        asset_ids=tuple(asset_ids),
        product_policy=OrderPolicySnapshot(
            template_id="classic",
            product_code="generation_single_base",
            catalog_version_id=uuid.uuid4(),
            catalog_version="2026-07-10",
            catalog_release_sha="a" * 40,
            generation_mode="single",
            scene_tier="base",
            subject_count=1,
            director_mode=False,
            credit_cost=2,
            gatekeeper_policy_version="gatekeeper.v1",
            gatekeeper_passed=True,
        ),
        funding_policy=OrderFundingPolicySnapshot(
            generation_mode="single",
            subject_count=1,
            is_trial=False,
            identity_claim_id=None,
            attempts_in_rolling_24h=0,
            ready_trial_exists=False,
            allowed_lot_class="PAID_ONLY",
            scene_tier="base",
            director_mode=False,
        ),
        credit_cost=2,
    )


class OrderTransactionTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_durable_job_without_generation_outbox(self) -> None:
        user_id = uuid.uuid4()
        asset = MediaAsset(
            id=uuid.uuid4(),
            owner_user_id=user_id,
            role=MediaAssetRole.SOURCE,
            status=MediaAssetStatus.ACTIVE,
        )
        db = _OrderDb([asset])
        idempotency = IdempotencyAttempt(
            record_id=uuid.uuid4(),
            replayed=False,
            state=IdempotencyState.STARTED.value,
            response_status=None,
            response_json=None,
        )
        reservation = SimpleNamespace(id=uuid.uuid4())
        with (
            patch(
                "app.services.order_transaction_service.begin_idempotent_request",
                new=AsyncMock(return_value=idempotency),
            ),
            patch(
                "app.services.order_transaction_service.reserve_credits",
                new=AsyncMock(return_value=reservation),
            ) as reserve,
            patch(
                "app.services.order_transaction_service.complete_idempotent_request",
                new=AsyncMock(),
            ) as complete,
            patch(
                "app.services.order_transaction_service.require_server_runtime_execution_stamp",
                return_value=RuntimeExecutionStamp(
                    api_deployment_id="dpl_123",
                    runtime_bundle_id="rtb_" + "c" * 64,
                    backend_executor_digest="sha256:" + "d" * 64,
                ),
            ),
        ):
            result = await create_order_transaction(db, _command(user_id, [asset.id]), now=NOW)

        self.assertEqual(result.status, "QUEUED")
        self.assertEqual(result.status_url, f"/api/v1/orders/{result.order_id}")
        reserve.assert_awaited_once()
        jobs = [item for item in db.added if isinstance(item, GenerationJob)]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].order_id, result.order_id)
        self.assertFalse(
            any(type(item).__name__ == "OutboxEvent" for item in db.added)
        )
        complete.assert_awaited_once()

    async def test_replay_returns_stored_response_without_assets_or_reservation(self) -> None:
        order_id = uuid.uuid4()
        replay = IdempotencyAttempt(
            record_id=uuid.uuid4(),
            replayed=True,
            state=IdempotencyState.COMPLETED.value,
            response_status=202,
            response_json={
                "order_id": str(order_id),
                "status": "QUEUED",
                "status_url": f"/api/v1/orders/{order_id}",
            },
        )
        db = _OrderDb([])
        with (
            patch(
                "app.services.order_transaction_service.begin_idempotent_request",
                new=AsyncMock(return_value=replay),
            ),
            patch(
                "app.services.order_transaction_service.reserve_credits",
                new=AsyncMock(),
            ) as reserve,
        ):
            result = await create_order_transaction(
                db,
                _command(uuid.uuid4(), [uuid.uuid4()]),
                now=NOW,
            )
        self.assertEqual(result.order_id, order_id)
        reserve.assert_not_awaited()
        self.assertEqual(db.added, [])


class PartnerOrderTransactionTest(unittest.IsolatedAsyncioTestCase):
    async def test_partner_order_locks_lineage_and_funding_before_atomic_graph_binding(self) -> None:
        host_user_id = uuid.uuid4()
        partner_user_id = uuid.uuid4()
        host_asset_id = uuid.uuid4()
        partner_asset_id = uuid.uuid4()
        consent_event_id = uuid.uuid4()
        invite = SimpleNamespace(
            id=uuid.uuid4(),
            host_user_id=host_user_id,
            partner_user_id=partner_user_id,
            status=PartnerInviteStatus.CONSENTED,
            version=3,
            expires_at=NOW.replace(day=14),
            order_intent_id=uuid.uuid4(),
            order_intent_hash="c" * 64,
            intent_policy_version="partner-consent.v1",
            template_id="royal_castle",
            partner_asset_id=partner_asset_id,
            partner_asset_sha256="p" * 64,
            consent_event_id=consent_event_id,
            order_id=None,
            job_id=None,
            accepted_at=NOW,
            consented_at=NOW,
            completed_at=None,
            revoked_at=None,
            cancelled_at=None,
            purpose="COUPLE",
        )
        consent_event = SimpleNamespace(
            id=consent_event_id,
            invite_id=invite.id,
            command="CONSENT",
            invite_version=3,
        )
        assets = (
            SimpleNamespace(id=host_asset_id, owner_user_id=host_user_id),
            SimpleNamespace(id=partner_asset_id, owner_user_id=partner_user_id),
        )
        command = _command(host_user_id, [host_asset_id])
        command = replace(
            command,
            asset_ids=(host_asset_id, partner_asset_id),
            asset_owner_ids=(host_user_id, partner_user_id),
        )
        accepted = SimpleNamespace(
            order_id=uuid.uuid4(),
            status="QUEUED",
            status_url="/api/v1/orders/result",
        )
        graph = SimpleNamespace(
            accepted=accepted,
            order=SimpleNamespace(id=accepted.order_id),
            job=SimpleNamespace(id=uuid.uuid4()),
        )
        db = _OrderDb([])
        events: list[str] = []

        async def locked_invite(*_args, **_kwargs):
            events.append("invite")
            return invite

        async def locked_consent(*_args, **_kwargs):
            events.append("consent")
            return consent_event

        async def locked_assets(*_args, **_kwargs):
            events.append("assets")
            return assets

        async def locked_funding(*_args, **_kwargs):
            events.append("funding")
            return SimpleNamespace()

        async def created_graph(*_args, **_kwargs):
            events.append("graph")
            return graph

        with (
            patch(
                "app.services.partner_invite_service._lock_partner_invite",
                side_effect=locked_invite,
            ),
            patch(
                "app.services.partner_invite_service._lock_consent_event",
                side_effect=locked_consent,
            ),
            patch(
                "app.services.partner_invite_service._lock_partner_order_assets",
                side_effect=locked_assets,
            ),
            patch(
                "app.services.partner_invite_service._build_partner_order_command",
                AsyncMock(return_value=command),
            ),
            patch(
                "app.services.partner_invite_service.lock_credit_funding",
                side_effect=locked_funding,
            ),
            patch(
                "app.services.partner_invite_service.create_order_transaction_graph",
                side_effect=created_graph,
            ),
        ):
            result = await create_partner_order(
                db,
                invite_id=invite.id,
                host_user_id=host_user_id,
                expected_version=3,
                host_asset_id=host_asset_id,
                consent_event_id=consent_event_id,
                idempotency_key="partner-order-once",
                request_id="partner-order-request",
                now=NOW,
            )

        self.assertIs(result, accepted)
        self.assertEqual(events, ["invite", "consent", "assets", "funding", "graph"])
        self.assertEqual(invite.order_id, graph.order.id)
        self.assertEqual(invite.job_id, graph.job.id)
        self.assertEqual(invite.status, PartnerInviteStatus.COMPLETED)
        self.assertEqual(invite.version, 4)
        transition = next(item for item in db.added if isinstance(item, PartnerInviteEvent))
        self.assertEqual(transition.command, "COMPLETE_ORDER")


if __name__ == "__main__":
    unittest.main()
