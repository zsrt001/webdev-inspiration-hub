#!/usr/bin/env python3
"""Validate signed Creem test-mode behavior evidence without activating source contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlsplit


CREEM_CASES = {
    "CREEM_DASHBOARD_REFUND_CONFIRMATION": {
        "capability": "dashboard_refund_confirmation",
        "required_facts": {
            "exact_purchase_ref",
            "dashboard_refund_initiated",
            "full_refund_event_signed",
            "transaction_refund_state_confirmed",
            "reversal_linked",
            "webhook_replay_deduped",
        },
    },
    "CREEM_SUBSCRIPTION_PAID_TRANSACTION": {
        "capability": "subscription_paid_transaction",
        "required_facts": {
            "initial_paid_transaction_stable",
            "renewal_paid_transaction_stable",
            "invoice_transaction_unique",
            "subscription_period_unique",
            "duplicate_event_deduped",
            "out_of_order_event_reconciled",
            "past_due_recovered_by_new_transaction",
        },
    },
    "CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION": {
        "capability": "subscription_period_end_cancellation",
        "required_facts": {
            "exact_subscription_ref",
            "period_end_cancel_confirmed",
            "active_until_period_end",
            "request_replay_deduped",
        },
    },
}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _official_contract_hash(contract: dict[str, Any], *, capability: str, now: datetime) -> str:
    required = {
        "schema", "provider", "capability", "official_source_url", "official_version",
        "endpoint_schema_sha256", "authentication_scheme", "correlation_semantics",
        "idempotency_semantics", "retry_taxonomy", "retrieved_at",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise ValueError("official Creem contract shape is invalid")
    source = urlsplit(str(contract.get("official_source_url") or ""))
    if (
        contract.get("schema") != "vowpic.provider-official-contract.v1"
        or contract.get("provider") != "creem"
        or contract.get("capability") != capability
        or source.scheme != "https"
        or source.hostname != "docs.creem.io"
        or source.port not in {None, 443}
        or source.username is not None
        or source.password is not None
    ):
        raise ValueError("official Creem contract identity is invalid")
    if capability == "dashboard_refund_confirmation" and source.path.rstrip("/") != "/features/subscriptions/refunds-and-cancellations":
        raise ValueError("official Creem refund workflow source is invalid")
    if not str(contract.get("official_version") or "").strip() or not _SHA64.fullmatch(str(contract.get("endpoint_schema_sha256") or "")):
        raise ValueError("official Creem contract metadata is invalid")
    for field in ("authentication_scheme", "correlation_semantics", "idempotency_semantics", "retry_taxonomy"):
        if not 16 <= len(str(contract.get(field) or "").strip()) <= 1000:
            raise ValueError(f"Creem {field} is incomplete")
    retrieved_at = _timestamp(contract.get("retrieved_at"), label="Creem retrieved_at")
    if retrieved_at > now + timedelta(minutes=5) or now - retrieved_at > timedelta(days=30):
        raise ValueError("official Creem contract snapshot is stale or from the future")
    return hashlib.sha256(canonical_json_bytes(contract)).hexdigest()


def _sandbox_report_hash(
    report: dict[str, Any], *, capability: str, required_facts: set[str], source_sha: str,
    official_hash: str, signing_key: bytes, now: datetime,
) -> str:
    required = {
        "schema", "provider", "capability", "tested_source_sha", "official_contract_sha256",
        "passed", "proof_id_hash", "request_count", "replay_count", "duplicate_effect_count",
        "verified_facts", "produced_at", "approval_ref", "signature",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("Creem test report shape is invalid")
    facts = report.get("verified_facts")
    if (
        report.get("schema") != "vowpic.creem-sandbox-report.v1"
        or report.get("provider") != "creem"
        or report.get("capability") != capability
        or report.get("tested_source_sha") != source_sha
        or report.get("official_contract_sha256") != official_hash
        or report.get("passed") is not True
        or not _SHA64.fullmatch(str(report.get("proof_id_hash") or ""))
        or report.get("request_count") != 1
        or not isinstance(report.get("replay_count"), int)
        or report["replay_count"] < 1
        or report.get("duplicate_effect_count") != 0
        or not isinstance(facts, dict)
        or set(facts) != required_facts
        or any(value is not True for value in facts.values())
        or not str(report.get("approval_ref") or "").strip()
    ):
        raise ValueError("Creem test report does not prove the required behavior")
    produced_at = _timestamp(report.get("produced_at"), label="Creem produced_at")
    if produced_at > now + timedelta(minutes=5) or now - produced_at > timedelta(hours=2):
        raise ValueError("Creem test report is stale or from the future")
    if len(signing_key) < 32:
        raise ValueError("Provider evidence signing key is missing or too short")
    match = _SIGNATURE.fullmatch(str(report.get("signature") or ""))
    unsigned = {key: value for key, value in report.items() if key != "signature"}
    expected = hmac.new(signing_key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), expected):
        raise ValueError("Creem test report signature is invalid")
    return hashlib.sha256(canonical_json_bytes(report)).hexdigest()


def verify_creem_test_evidence(
    evidence_bundle: dict[str, Any], *, expected_source_sha: str, signing_key: bytes,
    now: datetime | None = None,
) -> dict[str, str]:
    if (
        not isinstance(evidence_bundle, dict)
        or set(evidence_bundle) != {"schema", "official_contracts", "sandbox_reports"}
        or evidence_bundle.get("schema") != "vowpic.creem-test-evidence.v1"
        or not isinstance(evidence_bundle.get("official_contracts"), dict)
        or not isinstance(evidence_bundle.get("sandbox_reports"), dict)
        or set(evidence_bundle["official_contracts"]) != set(CREEM_CASES)
        or set(evidence_bundle["sandbox_reports"]) != set(CREEM_CASES)
    ):
        raise ValueError("Creem test evidence bundle is invalid")
    source_sha = str(expected_source_sha or "").strip().lower()
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("Creem tested source SHA is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hashes: dict[str, str] = {}
    approval_refs: set[str] = set()
    for name, spec in CREEM_CASES.items():
        official_hash = _official_contract_hash(
            evidence_bundle["official_contracts"][name],
            capability=spec["capability"],
            now=current,
        )
        report = evidence_bundle["sandbox_reports"][name]
        hashes[name] = _sandbox_report_hash(
            report,
            capability=spec["capability"],
            required_facts=spec["required_facts"],
            source_sha=source_sha,
            official_hash=official_hash,
            signing_key=signing_key,
            now=current,
        )
        approval_refs.add(str(report["approval_ref"]))
    if len(approval_refs) != 1:
        raise ValueError("Creem test evidence approval reference is inconsistent")
    return hashes
