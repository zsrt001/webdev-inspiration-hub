"""Fail-closed durable generation state transitions."""

import unittest
import uuid

from app.models.generation_attempt import (
    GenerationAttempt,
    GenerationAttemptKind,
    GenerationAttemptStatus,
)
from app.models.generation_job import GenerationJob, GenerationJobStatus
from app.services.generation_job_service import (
    GenerationStateError,
    validate_attempt_transition,
    validate_job_transition,
)


class GenerationStateConstraintTest(unittest.TestCase):
    def test_job_and_attempt_status_sets_are_exact(self) -> None:
        self.assertEqual(
            {item.value for item in GenerationJobStatus},
            {"QUEUED", "ACTIVE", "RECONCILING", "FINISHED", "FAILED", "CANCELLED"},
        )
        self.assertEqual(
            {item.value for item in GenerationAttemptStatus},
            {"PREPARED", "SUBMITTING", "SUBMITTED", "UNKNOWN", "FINISHED", "FAILED"},
        )

    def test_unknown_or_regressive_transitions_fail_closed(self) -> None:
        validate_job_transition(GenerationJobStatus.QUEUED, GenerationJobStatus.ACTIVE)
        validate_attempt_transition(
            GenerationAttemptStatus.PREPARED,
            GenerationAttemptStatus.SUBMITTING,
        )
        for current, target in (
            (GenerationJobStatus.FINISHED, GenerationJobStatus.ACTIVE),
            (GenerationJobStatus.QUEUED, "invented"),
        ):
            with self.subTest(current=current, target=target), self.assertRaises(GenerationStateError):
                validate_job_transition(current, target)

    def test_initial_attempt_copies_unpredictable_job_correlation(self) -> None:
        correlation = uuid.uuid4()
        job = GenerationJob.queued(
            order_id=uuid.uuid4(),
            submission_correlation_id=correlation,
            api_deployment_id="dpl_123",
            runtime_bundle_id="a" * 64,
            expected_worker_image_digest="sha256:" + "b" * 64,
        )
        attempt = GenerationAttempt.prepared(
            job=job,
            attempt_number=1,
            kind=GenerationAttemptKind.INITIAL,
            provider="evolink",
        )
        self.assertEqual(attempt.client_request_id, str(correlation))
        self.assertEqual(job.payload_version, "generation-job.v1")
        self.assertNotEqual(str(correlation), str(job.order_id))


if __name__ == "__main__":
    unittest.main()
