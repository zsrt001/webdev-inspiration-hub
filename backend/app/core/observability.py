"""Fail-closed structured observability contracts.

This module deliberately accepts only correlation identifiers. Business payloads,
credentials, personal data, and storage locations must never enter event fields.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


class ObservabilityContractError(ValueError):
    """Raised before an unsafe or ambiguous event can be emitted."""


CORRELATION_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "order_id",
        "reservation_id",
        "outbox_event_id",
        "job_id",
        "attempt_id",
        "provider_job_id",
        "artifact_id",
        "purchase_id",
        "payment_event_id",
        "deployment_id",
        "git_sha",
    }
)

SENSITIVE_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "csrf_secret",
        "email",
        "image_bytes",
        "embedding",
        "object_url",
        "payment_secret",
        "payment_credential",
        "internal_path",
    }
)

MANDATORY_METRICS = frozenset(
    {
        "auth_rejection_total",
        "cross_user_denial_total",
        "upload_rejection_total",
        "outbox_backlog_count",
        "outbox_oldest_age_seconds",
        "queue_latency_seconds",
        "lease_recovery_total",
        "provider_latency_seconds",
        "provider_cost_total",
        "qa_reject_total",
        "qa_repair_total",
        "watermark_failure_total",
        "download_denial_total",
        "webhook_unhandled_total",
        "ledger_reconciliation_failure_total",
        "deletion_failure_total",
        "cleanup_last_success_timestamp_seconds",
    }
)

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")
_EMAIL = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_INTERNAL_PATH = re.compile(r"(?i)(?:\b[a-z]:\\|/(?:home|users|var|tmp|private)/)")


def _validated_identifier(field: str, value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview, list, dict, tuple, set)):
        raise ObservabilityContractError(f"{field} contains a prohibited payload type")
    text = str(value or "").strip()
    if not text or len(text) > 160:
        raise ObservabilityContractError(f"{field} must be a bounded identifier")
    if _EMAIL.search(text):
        raise ObservabilityContractError(f"{field} contains an email address")
    if _INTERNAL_PATH.search(text):
        raise ObservabilityContractError(f"{field} contains an internal path")
    if text.lower().startswith(("http://", "https://", "bearer ")):
        raise ObservabilityContractError(f"{field} contains a prohibited URL or credential")
    return text


def _hashed_user_id(value: str) -> str:
    return "uid_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def build_structured_event(event: str, **fields: Any) -> dict[str, str]:
    """Build one deterministic, correlation-only event ready for JSON logging."""
    clean_event = str(event or "").strip()
    if not _EVENT_NAME.fullmatch(clean_event):
        raise ObservabilityContractError("event name is invalid")
    prohibited = SENSITIVE_FIELDS & set(fields)
    if prohibited:
        raise ObservabilityContractError(
            f"sensitive fields are prohibited: {', '.join(sorted(prohibited))}"
        )
    unexpected = set(fields) - CORRELATION_FIELDS
    if unexpected:
        raise ObservabilityContractError(
            f"event fields are not allowlisted: {', '.join(sorted(unexpected))}"
        )

    result = {"event": clean_event}
    for field, value in fields.items():
        clean_value = _validated_identifier(field, value)
        result[field] = _hashed_user_id(clean_value) if field == "user_id" else clean_value
    return result
