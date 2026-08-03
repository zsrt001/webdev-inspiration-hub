"""Durable generation fact schema contract."""

from pathlib import Path
import unittest

from app.models.asset_access_grant import AssetAccessGrant
from app.models.credit_reservation import CreditReservation
from app.models.generation_attempt import GenerationAttempt
from app.models.generation_job import GenerationJob
from app.models.media_asset import MediaAsset
from app.models.qa_verdict import QaVerdict


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260710_0019_generation_jobs.py"


class GenerationJobMigrationTest(unittest.TestCase):
    def test_revision_tables_guards_and_service_rls_are_declared(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision = "20260710_0019"', source)
        self.assertIn('down_revision = "20260710_0018"', source)
        for table in ("generation_jobs", "generation_attempts", "qa_verdicts"):
            self.assertIn(f'"{table}"', source)
        for token in (
            "uq_generation_jobs_nonterminal_order",
            "uq_generation_jobs_active_lease_claim",
            "uq_generation_attempt_initial_job",
            "submission_correlation_id",
            "generation-job.v1",
            "guard_generation_job_transition",
            "guard_generation_attempt_transition",
            "qa_verdict_append_only_guard",
            "qa verdict lineage mismatch",
            "active attempt belongs to another job",
            "submission_accounting_state IN",
            "heartbeat_at IS NULL OR lease_owner IS NOT NULL",
            "ENABLE ROW LEVEL SECURITY",
            "vowpic_generation_service",
        ):
            self.assertIn(token, source)
        self.assertEqual(
            source.count("RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER"),
            3,
        )

    def test_models_expose_runtime_fencing_attempt_and_verdict_lineage(self) -> None:
        for column in (
            "order_id",
            "submission_correlation_id",
            "status",
            "lease_owner",
            "lease_claim_id",
            "lease_expires_at",
            "heartbeat_at",
            "fencing_token",
            "payload_version",
            "api_deployment_id",
            "runtime_bundle_id",
            "expected_worker_image_digest",
            "settlement_status",
            "delivery_status",
        ):
            self.assertIn(column, GenerationJob.__table__.columns)
        for column in (
            "job_id",
            "attempt_number",
            "kind",
            "status",
            "client_request_id",
            "provider_job_id",
            "result_asset_id",
        ):
            self.assertIn(column, GenerationAttempt.__table__.columns)
        self.assertIn("candidate_asset_id", QaVerdict.__table__.columns)
        self.assertEqual(
            next(iter(MediaAsset.__table__.c.job_id.foreign_keys)).target_fullname,
            "generation_jobs.id",
        )
        self.assertEqual(
            next(iter(AssetAccessGrant.__table__.c.attempt_id.foreign_keys)).target_fullname,
            "generation_attempts.id",
        )
        self.assertEqual(
            next(iter(CreditReservation.__table__.c.provider_attempt_id.foreign_keys)).target_fullname,
            "generation_attempts.id",
        )
        job_indexes = {index.name for index in GenerationJob.__table__.indexes}
        attempt_indexes = {index.name for index in GenerationAttempt.__table__.indexes}
        self.assertIn("uq_generation_jobs_active_lease_claim", job_indexes)
        self.assertIn("uq_generation_attempt_initial_job", attempt_indexes)


if __name__ == "__main__":
    unittest.main()
