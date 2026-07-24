"""Fail-closed in-memory validation shared by durable job writers."""

from __future__ import annotations

from app.models.generation_attempt import GenerationAttemptStatus
from app.models.generation_job import GenerationJobStatus


class GenerationStateError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_JOB_TRANSITIONS = {
    GenerationJobStatus.QUEUED: {GenerationJobStatus.ACTIVE, GenerationJobStatus.CANCELLED},
    GenerationJobStatus.ACTIVE: {
        GenerationJobStatus.RECONCILING,
        GenerationJobStatus.FINISHED,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.RECONCILING: {
        GenerationJobStatus.ACTIVE,
        GenerationJobStatus.FINISHED,
        GenerationJobStatus.FAILED,
        GenerationJobStatus.CANCELLED,
    },
    GenerationJobStatus.FINISHED: set(),
    GenerationJobStatus.FAILED: set(),
    GenerationJobStatus.CANCELLED: set(),
}

_ATTEMPT_TRANSITIONS = {
    GenerationAttemptStatus.PREPARED: {
        GenerationAttemptStatus.SUBMITTING,
        GenerationAttemptStatus.FAILED,
    },
    GenerationAttemptStatus.SUBMITTING: {
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.UNKNOWN,
        GenerationAttemptStatus.FAILED,
    },
    GenerationAttemptStatus.SUBMITTED: {
        GenerationAttemptStatus.FINISHED,
        GenerationAttemptStatus.FAILED,
        GenerationAttemptStatus.UNKNOWN,
    },
    GenerationAttemptStatus.UNKNOWN: {
        # Used only by the protected operator settlement path after Provider
        # evidence proves that the ambiguous submission was not accepted.
        GenerationAttemptStatus.PREPARED,
        GenerationAttemptStatus.SUBMITTED,
        GenerationAttemptStatus.FINISHED,
        GenerationAttemptStatus.FAILED,
    },
    GenerationAttemptStatus.FINISHED: set(),
    GenerationAttemptStatus.FAILED: set(),
}


def validate_job_transition(current, target) -> GenerationJobStatus:
    try:
        current_status = GenerationJobStatus(current)
        target_status = GenerationJobStatus(target)
    except (TypeError, ValueError) as exc:
        raise GenerationStateError("generation_job_status_unknown") from exc
    if target_status not in _JOB_TRANSITIONS[current_status]:
        raise GenerationStateError("generation_job_transition_invalid")
    return target_status


def validate_attempt_transition(current, target) -> GenerationAttemptStatus:
    try:
        current_status = GenerationAttemptStatus(current)
        target_status = GenerationAttemptStatus(target)
    except (TypeError, ValueError) as exc:
        raise GenerationStateError("generation_attempt_status_unknown") from exc
    if target_status not in _ATTEMPT_TRANSITIONS[current_status]:
        raise GenerationStateError("generation_attempt_transition_invalid")
    return target_status
