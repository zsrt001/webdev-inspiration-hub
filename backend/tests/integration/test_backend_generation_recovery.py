"""Real PostgreSQL website-backend crash-recovery verification gate."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import asyncpg
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.models.admin_audit_log import AdminAuditLog
from app.models.credit_reservation import CreditReservation, ReservationStatus
from app.models.credit_transaction import CreditTransaction
from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.models.order import Order, OrderStatus
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.models.user import User
from app.routers import admin
from app.services import generation_executor_service as executor
from app.services import generation_manual_settlement_service as manual_settlement
from app.services.job_lease_service import (
    JobRequiresReconciliation,
    StaleWorkerFence,
    claim_generation_job,
    complete_generation_job,
)
from app.services.legacy_outbox_retirement_service import (
    inventory_legacy_outbox,
    retire_legacy_outbox,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _asyncpg_url(database_url: str) -> str:
    parsed = make_url(str(database_url or "").strip())
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("backend generation integration URL must be PostgreSQL")
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


class BackendGenerationRecoveryUrlContractTest(unittest.TestCase):
    def test_plain_ci_postgresql_url_is_upgraded_to_asyncpg(self) -> None:
        normalized = make_url(
            _asyncpg_url(
                "postgresql://postgres:postgres@127.0.0.1:5432/vowpic_rls_test"
            )
        )
        self.assertEqual(normalized.drivername, "postgresql+asyncpg")
        self.assertEqual(normalized.database, "vowpic_rls_test")


class _EvidenceStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_private(self, object_key: str, data: bytes, _content_type: str) -> None:
        if object_key in self.objects:
            raise RuntimeError("already exists")
        self.objects[object_key] = bytes(data)

    def read_private(self, object_key: str) -> bytes:
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        return self.objects[object_key]


async def _create_database(base_url, database_name: str) -> None:
    connection = await asyncpg.connect(
        host=base_url.host,
        port=base_url.port or 5432,
        user=base_url.username,
        password=base_url.password,
        database="postgres",
    )
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def _drop_database(base_url, database_name: str) -> None:
    connection = await asyncpg.connect(
        host=base_url.host,
        port=base_url.port or 5432,
        user=base_url.username,
        password=base_url.password,
        database="postgres",
    )
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await connection.close()


@unittest.skipUnless(
    os.getenv("RUN_BACKEND_GENERATION_INTEGRATION") == "1",
    "requires an explicit real local PostgreSQL integration environment",
)
class BackendGenerationRecoveryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        raw_database_url = os.getenv(
            "BACKEND_GENERATION_INTEGRATION_DATABASE_URL",
            "",
        ).strip()
        if not raw_database_url:
            raise RuntimeError(
                "BACKEND_GENERATION_INTEGRATION_DATABASE_URL is required"
            )

        base_url = make_url(raw_database_url)
        if base_url.get_backend_name() != "postgresql":
            raise RuntimeError("backend generation integration database must be PostgreSQL")
        if base_url.host not in LOCAL_HOSTS:
            raise RuntimeError("backend generation integration database must be local")
        if "test" not in str(base_url.database or "").lower():
            raise RuntimeError(
                "backend generation integration PostgreSQL database must be test-only"
            )

        cls.base_database_url = base_url
        cls.database_name = f"vowpic_backend_it_{secrets.token_hex(6)}"
        temp_url = base_url.set(database=cls.database_name)
        cls.database_url = temp_url.render_as_string(hide_password=False)
        cls.async_database_url = _asyncpg_url(cls.database_url)
        asyncio.run(_create_database(base_url, cls.database_name))

        migration_env = os.environ.copy()
        migration_env["DATABASE_URL"] = cls.database_url
        migration_env["PYTHONIOENCODING"] = "utf-8"
        try:
            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=BACKEND_DIR,
                env=migration_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except Exception:
            asyncio.run(_drop_database(base_url, cls.database_name))
            cls.database_name = None
            raise
        if migration.returncode != 0:
            asyncio.run(_drop_database(base_url, cls.database_name))
            cls.database_name = None
            raise RuntimeError(
                "backend generation integration migration failed:\n"
                + (migration.stderr or migration.stdout)[-4000:]
            )

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "database_name", None):
            asyncio.run(_drop_database(cls.base_database_url, cls.database_name))
        super().tearDownClass()

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            self.async_database_url,
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _admin_request(path: str) -> Request:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 50000),
                "scheme": "http",
            }
        )
        request.state.admin_actor = "postgres-integration-reviewer"
        return request

    async def _record_evidence(
        self,
        db,
        *,
        job_id: uuid.UUID,
        reason: str,
        provider_task_id: str,
        store: _EvidenceStore,
    ):
        return await manual_settlement.create_generation_manual_evidence(
            db,
            job_id=job_id,
            action="BIND_PROVIDER_TASK",
            source_type="EVOLINK_DASHBOARD",
            observation_reference="integration-provider-console-record",
            observed_at=datetime.now(timezone.utc),
            approval_id=uuid.uuid4(),
            operator_actor="postgres-integration-reviewer",
            operator_reason=reason,
            provider_task_id=provider_task_id,
            evidence_store=store,
        )

    async def _create_manual_case(
        self,
        db,
        *,
        provider_job_id: str | None = None,
        manual: bool = True,
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
        user_id = uuid.uuid4()
        order_id = uuid.uuid4()
        db.add(User(id=user_id, role="user", status="active"))
        order = Order(
            id=order_id,
            user_id=user_id,
            status=OrderStatus.CREATED,
            price_cents=0,
        )
        db.add(order)
        await db.flush()

        job = GenerationJob.queued(
            order_id=order_id,
            submission_correlation_id=uuid.uuid4(),
            api_deployment_id="dpl_manual_settlement_integration",
            runtime_bundle_id="rtb_" + "c" * 64,
            expected_worker_image_digest="sha256:" + "d" * 64,
        )
        db.add(job)
        await db.flush()
        attempt = GenerationAttempt.prepared(
            job=job,
            attempt_number=1,
            kind=GenerationAttemptKind.INITIAL,
            provider="evolink",
        )
        db.add(attempt)
        await db.flush()
        attempt.status = GenerationAttemptStatus.SUBMITTING
        attempt.provider_job_id = provider_job_id
        attempt.submit_started_at = datetime.now(timezone.utc)
        job.active_attempt_id = attempt.id
        job.status = GenerationJobStatus.ACTIVE
        await db.flush()
        attempt.status = (
            GenerationAttemptStatus.UNKNOWN
            if manual
            else GenerationAttemptStatus.SUBMITTED
        )
        await db.flush()
        if not manual:
            attempt.status = GenerationAttemptStatus.FINISHED
            await db.flush()
        reservation = CreditReservation(
            id=uuid.uuid4(),
            user_id=user_id,
            order_id=order_id,
            amount=1,
            status=ReservationStatus.RESERVED,
            idempotency_key=f"manual-settlement:{order_id}",
            request_hash="e" * 64,
            funding_policy_snapshot={"version": "integration.v1"},
            funding_policy_hash="f" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(reservation)
        await db.flush()

        job.status = (
            GenerationJobStatus.RECONCILING
            if manual
            else GenerationJobStatus.FINISHED
        )
        job.last_error_code = (
            "provider_submission_human_required" if manual else None
        )
        order.generation_job_id = job.id
        order.reservation_id = reservation.id
        order.status = (
            OrderStatus.UNKNOWN_EXTERNAL_STATE
            if manual
            else OrderStatus.COMPLETED
        )
        await db.commit()
        return job.id, order.id, attempt.id, reservation.id

    async def test_admin_route_preserves_manual_queue_and_audits_resolution(
        self,
    ) -> None:
        async with self.sessions() as db:
            job_id, order_id, attempt_id, reservation_id = (
                await self._create_manual_case(db)
            )

            with self.assertRaises(HTTPException) as caught:
                await admin.update_order_status(
                    str(order_id),
                    admin.UpdateStatusRequest(status="CREATED"),
                    self._admin_request(
                        f"/api/v1/admin/orders/{order_id}/status"
                    ),
                    db,
                )
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(
                caught.exception.detail["code"],
                "generation_status_requires_settlement_workflow",
            )

            queued = await admin.list_admin_generation_manual_settlements(
                limit=100,
                db=db,
            )
            self.assertEqual(queued.total, 1)
            self.assertEqual(queued.cases[0].job_id, job_id)

            reason = "Verified in the EvoLink provider console."
            store = _EvidenceStore()
            with patch.object(manual_settlement, "StorageService", return_value=store):
                recorded = await admin.create_admin_generation_manual_evidence(
                    job_id,
                    admin.AdminGenerationManualEvidenceRequest(
                        action="BIND_PROVIDER_TASK",
                        source_type="EVOLINK_DASHBOARD",
                        observation_reference="integration-provider-console-record",
                        observed_at=datetime.now(timezone.utc),
                        approval_id=uuid.uuid4(),
                        operator_reason=reason,
                        provider_task_id="task_route_integration",
                    ),
                    self._admin_request(
                        f"/api/v1/admin/generation/manual-settlements/{job_id}/evidence"
                    ),
                    db,
                )
                response = await admin.resolve_admin_generation_manual_settlement(
                    job_id,
                    admin.AdminGenerationManualResolutionRequest(
                        action="BIND_PROVIDER_TASK",
                        provider_evidence_object_key=recorded.provider_evidence_object_key,
                        provider_evidence_sha256=recorded.provider_evidence_sha256,
                        operator_reason=reason,
                        provider_task_id="task_route_integration",
                    ),
                    self._admin_request(
                        f"/api/v1/admin/generation/manual-settlements/{job_id}/resolve"
                    ),
                    db,
                )
            await db.commit()

            self.assertEqual(response.next_action, "RECONCILE_PROVIDER_TASK")
            attempt = await db.get(GenerationAttempt, attempt_id)
            order = await db.get(Order, order_id)
            reservation = await db.get(CreditReservation, reservation_id)
            audit = await db.scalar(
                select(AdminAuditLog).where(
                    AdminAuditLog.action
                    == "resolve_generation_ambiguous_submission"
                )
            )
            transaction_count = int(
                await db.scalar(
                    select(func.count(CreditTransaction.id)).where(
                        CreditTransaction.provider_attempt_id == attempt_id
                    )
                )
                or 0
            )
            self.assertEqual(
                attempt.status,
                GenerationAttemptStatus.SUBMITTED,
            )
            self.assertEqual(
                attempt.provider_job_id,
                "task_route_integration",
            )
            self.assertEqual(order.status, OrderStatus.GENERATING)
            self.assertEqual(reservation.status, ReservationStatus.RESERVED)
            self.assertIsNotNone(audit)
            self.assertEqual(transaction_count, 0)
            self.assertEqual(
                await manual_settlement.count_generation_manual_cases(db),
                0,
            )

    async def test_duplicate_and_concurrent_provider_task_binding_return_conflict(
        self,
    ) -> None:
        async with self.sessions() as setup:
            await self._create_manual_case(
                setup,
                provider_job_id="task_preexisting",
                manual=False,
            )
            duplicate_job_id, _order_id, _attempt_id, _reservation_id = (
                await self._create_manual_case(setup)
            )
            reason = "Verified in the EvoLink provider console."
            store = _EvidenceStore()
            recorded = await self._record_evidence(
                setup,
                job_id=duplicate_job_id,
                reason=reason,
                provider_task_id="task_preexisting",
                store=store,
            )

            with self.assertRaises(HTTPException) as duplicate:
                with patch.object(
                    manual_settlement,
                    "StorageService",
                    return_value=store,
                ):
                    await admin.resolve_admin_generation_manual_settlement(
                        duplicate_job_id,
                        admin.AdminGenerationManualResolutionRequest(
                            action="BIND_PROVIDER_TASK",
                            provider_evidence_object_key=recorded.object_key,
                            provider_evidence_sha256=recorded.sha256,
                            operator_reason=reason,
                            provider_task_id="task_preexisting",
                        ),
                        self._admin_request(
                            "/api/v1/admin/generation/manual-settlements/"
                            f"{duplicate_job_id}/resolve"
                        ),
                        setup,
                    )
            self.assertEqual(duplicate.exception.status_code, 409)
            self.assertEqual(
                duplicate.exception.detail["code"],
                "provider_task_already_bound",
            )
            await setup.rollback()

        async with self.sessions() as setup:
            first_job_id, *_ = await self._create_manual_case(setup)
            second_job_id, *_ = await self._create_manual_case(setup)

        async with self.sessions() as first, self.sessions() as second:
            store = _EvidenceStore()
            first_reason = "First concurrent provider fact is authoritative."
            second_reason = "Second concurrent provider fact must conflict."
            first_evidence = await self._record_evidence(
                first,
                job_id=first_job_id,
                reason=first_reason,
                provider_task_id="task_concurrent_unique",
                store=store,
            )
            second_evidence = await self._record_evidence(
                second,
                job_id=second_job_id,
                reason=second_reason,
                provider_task_id="task_concurrent_unique",
                store=store,
            )
            first_result = await manual_settlement.resolve_generation_manual_case(
                first,
                job_id=first_job_id,
                action="BIND_PROVIDER_TASK",
                provider_evidence_object_key=first_evidence.object_key,
                provider_evidence_sha256=first_evidence.sha256,
                operator_reason=first_reason,
                provider_task_id="task_concurrent_unique",
                evidence_store=store,
            )
            self.assertEqual(
                first_result.next_action,
                "RECONCILE_PROVIDER_TASK",
            )

            second_task = asyncio.create_task(
                manual_settlement.resolve_generation_manual_case(
                    second,
                    job_id=second_job_id,
                    action="BIND_PROVIDER_TASK",
                    provider_evidence_object_key=second_evidence.object_key,
                    provider_evidence_sha256=second_evidence.sha256,
                    operator_reason=second_reason,
                    provider_task_id="task_concurrent_unique",
                    evidence_store=store,
                )
            )
            await asyncio.sleep(0.1)
            await first.commit()
            with self.assertRaisesRegex(
                manual_settlement.GenerationManualSettlementError,
                "provider_task_already_bound",
            ):
                await asyncio.wait_for(second_task, timeout=5)
            await second.rollback()

    async def test_restart_routes_ambiguous_submit_to_query_without_replay(self) -> None:
        started_at = datetime.now(timezone.utc)
        user_id = uuid.uuid4()
        order_id = uuid.uuid4()
        job_id = uuid.uuid4()

        async with self.sessions() as db:
            db.add(User(id=user_id, role="user", status="active"))
            await db.flush()
            db.add(
                Order(
                    id=order_id,
                    user_id=user_id,
                    status=OrderStatus.QUEUED,
                    price_cents=0,
                )
            )
            await db.flush()
            job = GenerationJob.queued(
                order_id=order_id,
                submission_correlation_id=uuid.uuid4(),
                api_deployment_id="dpl_backend_integration",
                runtime_bundle_id="rtb_" + "a" * 64,
                expected_worker_image_digest="sha256:" + "b" * 64,
            )
            job.id = job_id
            db.add(job)
            await db.commit()

        async with self.sessions() as first_request:
            lease = await claim_generation_job(
                first_request,
                job_id=job_id,
                worker_id="api:before-crash",
                claim_id=uuid.uuid4(),
                now=started_at + timedelta(seconds=1),
            )
            await first_request.commit()

        provider_submission_count = 0
        async with self.sessions() as first_request:
            job = await first_request.get(GenerationJob, job_id)
            attempt = GenerationAttempt.prepared(
                job=job,
                attempt_number=1,
                kind=GenerationAttemptKind.INITIAL,
                provider="evolink",
            )
            first_request.add(attempt)
            await first_request.flush()
            attempt.status = GenerationAttemptStatus.SUBMITTING
            attempt.submit_started_at = started_at + timedelta(seconds=2)
            await first_request.commit()
            provider_submission_count += 1

        async with self.sessions() as db:
            job = await db.get(GenerationJob, job_id)
            job.lease_expires_at = started_at + timedelta(seconds=3)
            job.heartbeat_at = started_at + timedelta(seconds=2)
            await db.commit()

        async with self.sessions() as restarted_request:
            with self.assertRaises(JobRequiresReconciliation):
                await claim_generation_job(
                    restarted_request,
                    job_id=job_id,
                    worker_id="api:after-crash",
                    claim_id=uuid.uuid4(),
                    now=started_at + timedelta(seconds=4),
                )
            await restarted_request.commit()

        self.assertEqual(provider_submission_count, 1)
        async with self.sessions() as db:
            job = await db.get(GenerationJob, job_id)
            attempt = await db.scalar(
                select(GenerationAttempt).where(GenerationAttempt.job_id == job_id)
            )
            transaction_count = await db.scalar(
                select(func.count(CreditTransaction.id)).where(
                    CreditTransaction.user_id == user_id
                )
            )
            self.assertEqual(job.status, GenerationJobStatus.RECONCILING)
            self.assertIsNone(job.lease_owner)
            self.assertEqual(attempt.status, GenerationAttemptStatus.SUBMITTING)
            self.assertEqual(transaction_count, 0)

            with self.assertRaises(StaleWorkerFence):
                await complete_generation_job(
                    db,
                    job_id=job_id,
                    worker_id=lease.worker_id,
                    claim_id=lease.claim_id,
                    fencing_token=lease.fencing_token,
                    terminal_status=GenerationJobStatus.FINISHED,
                    now=started_at + timedelta(seconds=5),
                )

    async def test_legacy_outbox_retirement_is_audited_fail_closed_and_idempotent(
        self,
    ) -> None:
        source_sha = "7" * 40
        payment_event_id = uuid.uuid4()
        payment_outbox_id = uuid.uuid4()
        unknown_outbox_id = uuid.uuid4()
        current = datetime.now(timezone.utc)
        async with self.sessions() as db:
            db.add(
                PaymentEvent(
                    id=payment_event_id,
                    provider="creem",
                    event_id=f"legacy-{payment_event_id}",
                    event_type="checkout.completed",
                    processing_state=PaymentEventProcessingState.APPLIED,
                    processed_at=current,
                )
            )
            db.add_all(
                [
                    OutboxEvent(
                        id=payment_outbox_id,
                        aggregate_type="payment_event",
                        aggregate_id=payment_event_id,
                        event_type="payment.event.received",
                        dedupe_key=f"payment-event:{payment_event_id}",
                        payload_version="vowpic.payment-event.v1",
                        payload_json={
                            "payment_event_id": str(payment_event_id),
                            "payload_version": "vowpic.payment-event.v1",
                        },
                        status=OutboxEventStatus.PENDING,
                        attempt_count=0,
                        next_attempt_at=current,
                        fencing_token=0,
                    ),
                    OutboxEvent(
                        id=unknown_outbox_id,
                        aggregate_type="unknown_legacy",
                        aggregate_id=uuid.uuid4(),
                        event_type="UNKNOWN_LEGACY",
                        dedupe_key=f"unknown:{unknown_outbox_id}",
                        payload_version="unknown.v1",
                        payload_json={"id": str(unknown_outbox_id)},
                        status=OutboxEventStatus.FAILED,
                        attempt_count=3,
                        next_attempt_at=current,
                        fencing_token=0,
                    ),
                ]
            )
            await db.commit()

        async with self.sessions() as db:
            inventory = await inventory_legacy_outbox(db, source_sha=source_sha)
            self.assertEqual(inventory["active_count"], 2)
            self.assertEqual(inventory["retirable_count"], 1)
            self.assertEqual(inventory["blocked_count"], 1)
            result = await retire_legacy_outbox(
                db,
                source_sha=source_sha,
                expected_snapshot_sha256=inventory["snapshot_sha256"],
                now=current,
            )
            await db.commit()
            self.assertFalse(result["passed"])
            self.assertEqual(result["retired_event_ids"], [str(payment_outbox_id)])
            self.assertEqual(result["blocked_count"], 1)

        async with self.sessions() as db:
            payment_outbox = await db.get(OutboxEvent, payment_outbox_id)
            self.assertEqual(payment_outbox.status, OutboxEventStatus.DISPATCHED)
            self.assertIn("legacy_retired:payment_event_applied", payment_outbox.last_error)
            repeat_inventory = await inventory_legacy_outbox(
                db,
                source_sha=source_sha,
            )
            repeat = await retire_legacy_outbox(
                db,
                source_sha=source_sha,
                expected_snapshot_sha256=repeat_inventory["snapshot_sha256"],
                now=current,
            )
            await db.commit()
            self.assertEqual(repeat["retired_event_ids"], [])
            self.assertEqual(repeat["blocked_count"], 1)

        async with self.sessions() as db:
            unknown = await db.get(OutboxEvent, unknown_outbox_id)
            unknown.status = OutboxEventStatus.DISPATCHED
            unknown.dispatched_at = current
            await db.commit()
        async with self.sessions() as db:
            empty = await inventory_legacy_outbox(db, source_sha=source_sha)
            self.assertTrue(empty["passed"])
            self.assertEqual(empty["active_count"], 0)


if __name__ == "__main__":
    unittest.main()
