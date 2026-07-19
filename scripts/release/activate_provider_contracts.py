#!/usr/bin/env python3
"""Activate a Provider contract only from fresh, signed, release-bound evidence."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


DOCUMENT_SCHEMA = "vowpic.provider-contracts.v1"
EVOLINK_KEY = "EVOLINK_SUBMISSION_RECONCILIATION"
CREEM_CONTRACTS = {
    "CREEM_REFUND_CREATION": {
        "capability": "refund_creation",
        # Creem currently documents Dashboard-only refunds and a
        # refund.created webhook, but no refund-creation REST endpoint.  This
        # must stay None until a later reviewed source change cites a genuine
        # official API endpoint and implements it.
        "official_endpoint_path": None,
        "required_facts": {
            "exact_purchase_ref",
            "full_refund_event_signed",
            "reversal_linked",
            "request_replay_deduped",
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
ALLOWED_ACTIVATION_PATHS = frozenset({"release/provider-contracts.json", "docs/ai-worklog.md"})
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


def _validate_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict) or set(document) != {"schema", "contracts"}:
        raise ValueError("Provider contract document shape is invalid")
    if document.get("schema") != DOCUMENT_SCHEMA or not isinstance(document.get("contracts"), dict):
        raise ValueError("Provider contract document schema is invalid")
    required = {EVOLINK_KEY, *CREEM_CONTRACTS}
    if not required.issubset(document["contracts"]):
        raise ValueError("required Provider contract entry is missing")


def _validate_official_contract(contract: dict[str, Any], *, now: datetime) -> str:
    expected_fields = {
        "schema",
        "provider",
        "capability",
        "official_source_url",
        "official_version",
        "endpoint_schema_sha256",
        "correlation_semantics",
        "idempotency_semantics",
        "retrieved_at",
    }
    if not isinstance(contract, dict) or set(contract) != expected_fields:
        raise ValueError("official Provider contract shape is invalid")
    if (
        contract.get("schema") != "vowpic.provider-official-contract.v1"
        or contract.get("provider") != "evolink"
        or contract.get("capability") != "submission_reconciliation"
    ):
        raise ValueError("official Provider contract identity is invalid")
    source = urlsplit(str(contract.get("official_source_url") or ""))
    if source.scheme != "https" or not source.hostname or source.username or source.password:
        raise ValueError("official Provider source must be an exact HTTPS URL")
    if not str(contract.get("official_version") or "").strip():
        raise ValueError("official Provider version is required")
    endpoint_hash = str(contract.get("endpoint_schema_sha256") or "").strip().lower()
    if not _SHA64.fullmatch(endpoint_hash):
        raise ValueError("official endpoint schema hash is invalid")
    for field in ("correlation_semantics", "idempotency_semantics"):
        value = str(contract.get(field) or "").strip()
        if len(value) < 16 or len(value) > 1000:
            raise ValueError(f"{field} must describe the verified Provider behavior")
    retrieved_at = _timestamp(contract.get("retrieved_at"), label="official retrieved_at")
    if retrieved_at > now + timedelta(minutes=5) or now - retrieved_at > timedelta(days=30):
        raise ValueError("official Provider contract is stale or from the future")
    return hashlib.sha256(canonical_json_bytes(contract)).hexdigest()


def _validate_sandbox_report(
    report: dict[str, Any],
    *,
    expected_tested_source_sha: str,
    official_contract_sha256: str,
    signing_key: bytes,
    approval_ref: str,
    now: datetime,
) -> str:
    required = {
        "schema",
        "provider",
        "capability",
        "tested_source_sha",
        "official_contract_sha256",
        "passed",
        "lost_response_simulated",
        "recovered_task_id_hash",
        "submit_count",
        "duplicate_generation_count",
        "produced_at",
        "approval_ref",
        "signature",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("Evolink sandbox report shape is invalid")
    if (
        report.get("schema") != "vowpic.evolink-sandbox-report.v1"
        or report.get("provider") != "evolink"
        or report.get("capability") != "submission_reconciliation"
    ):
        raise ValueError("Evolink sandbox report identity is invalid")
    expected_sha = str(expected_tested_source_sha or "").strip().lower()
    if not _SHA40.fullmatch(expected_sha) or report.get("tested_source_sha") != expected_sha:
        raise ValueError("Evolink sandbox report tested source SHA mismatch")
    if report.get("official_contract_sha256") != official_contract_sha256:
        raise ValueError("Evolink sandbox report official-contract hash mismatch")
    if report.get("approval_ref") != approval_ref or not str(approval_ref or "").strip():
        raise ValueError("Evolink sandbox report approval reference mismatch")
    if (
        report.get("passed") is not True
        or report.get("lost_response_simulated") is not True
        or report.get("submit_count") != 1
        or report.get("duplicate_generation_count") != 0
        or not _SHA64.fullmatch(str(report.get("recovered_task_id_hash") or ""))
    ):
        raise ValueError("Evolink sandbox report does not prove one-task lost-response recovery")
    produced_at = _timestamp(report.get("produced_at"), label="sandbox produced_at")
    if produced_at > now + timedelta(minutes=5) or now - produced_at > timedelta(hours=2):
        raise ValueError("Evolink sandbox report is stale or from the future")
    if len(signing_key) < 32:
        raise ValueError("Provider evidence signing key is missing or too short")
    match = _SIGNATURE.fullmatch(str(report.get("signature") or ""))
    unsigned = {key: value for key, value in report.items() if key != "signature"}
    expected_signature = hmac.new(signing_key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), expected_signature):
        raise ValueError("Evolink sandbox report signature is invalid")
    return hashlib.sha256(canonical_json_bytes(report)).hexdigest()


def activate_evolink_contract(
    document: dict[str, Any],
    *,
    official_contract: dict[str, Any],
    sandbox_report: dict[str, Any],
    expected_tested_source_sha: str,
    signing_key: bytes,
    approval_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_document(document)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    official_hash = _validate_official_contract(official_contract, now=current)
    evidence_hash = _validate_sandbox_report(
        sandbox_report,
        expected_tested_source_sha=expected_tested_source_sha,
        official_contract_sha256=official_hash,
        signing_key=signing_key,
        approval_ref=approval_ref,
        now=current,
    )
    activated = copy.deepcopy(document)
    entry = activated["contracts"][EVOLINK_KEY]
    if not isinstance(entry, dict) or entry.get("state") != "UNVERIFIED":
        raise ValueError("Evolink contract is not in the expected UNVERIFIED state")
    entry.update(
        {
            "state": "VERIFIED",
            "official_source_url": official_contract["official_source_url"],
            "official_version": official_contract["official_version"],
            "official_contract_sha256": official_hash,
            "endpoint_schema_sha256": official_contract["endpoint_schema_sha256"],
            "test_evidence_sha256": evidence_hash,
            "tested_source_sha": expected_tested_source_sha,
            "evidence_created_at": sandbox_report["produced_at"],
            "approval_ref": approval_ref,
            "correlation_semantics": official_contract["correlation_semantics"],
            "idempotency_semantics": official_contract["idempotency_semantics"],
        }
    )
    return activated


def _validate_creem_official_contract(
    contract: dict[str, Any],
    *,
    capability: str,
    now: datetime,
) -> str:
    if capability == "refund_creation":
        raise ValueError(
            "official Creem refund creation API endpoint is not documented"
        )
    expected_fields = {
        "schema",
        "provider",
        "capability",
        "official_source_url",
        "official_version",
        "endpoint_schema_sha256",
        "authentication_scheme",
        "correlation_semantics",
        "idempotency_semantics",
        "retry_taxonomy",
        "retrieved_at",
    }
    if not isinstance(contract, dict) or set(contract) != expected_fields:
        raise ValueError("official Creem contract shape is invalid")
    if (
        contract.get("schema") != "vowpic.provider-official-contract.v1"
        or contract.get("provider") != "creem"
        or contract.get("capability") != capability
    ):
        raise ValueError("official Creem contract identity is invalid")
    source = urlsplit(str(contract.get("official_source_url") or ""))
    if (
        source.scheme != "https"
        or not source.hostname
        or source.username
        or source.password
        or not source.hostname.endswith("creem.io")
    ):
        raise ValueError("official Creem source must be an exact Creem HTTPS URL")
    if not str(contract.get("official_version") or "").strip():
        raise ValueError("official Creem version is required")
    if not _SHA64.fullmatch(
        str(contract.get("endpoint_schema_sha256") or "").strip().lower()
    ):
        raise ValueError("official Creem endpoint schema hash is invalid")
    for field in (
        "authentication_scheme",
        "correlation_semantics",
        "idempotency_semantics",
        "retry_taxonomy",
    ):
        text = str(contract.get(field) or "").strip()
        if len(text) < 16 or len(text) > 1000:
            raise ValueError(f"Creem {field} is incomplete")
    retrieved_at = _timestamp(contract.get("retrieved_at"), label="Creem retrieved_at")
    if retrieved_at > now + timedelta(minutes=5) or now - retrieved_at > timedelta(days=30):
        raise ValueError("official Creem contract is stale or from the future")
    return hashlib.sha256(canonical_json_bytes(contract)).hexdigest()


def _validate_creem_sandbox_report(
    report: dict[str, Any],
    *,
    capability: str,
    required_facts: set[str],
    expected_tested_source_sha: str,
    official_contract_sha256: str,
    signing_key: bytes,
    approval_ref: str,
    now: datetime,
) -> str:
    required = {
        "schema",
        "provider",
        "capability",
        "tested_source_sha",
        "official_contract_sha256",
        "passed",
        "proof_id_hash",
        "request_count",
        "replay_count",
        "duplicate_effect_count",
        "verified_facts",
        "produced_at",
        "approval_ref",
        "signature",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("Creem sandbox report shape is invalid")
    if (
        report.get("schema") != "vowpic.creem-sandbox-report.v1"
        or report.get("provider") != "creem"
        or report.get("capability") != capability
        or report.get("tested_source_sha") != expected_tested_source_sha
        or report.get("official_contract_sha256") != official_contract_sha256
        or report.get("approval_ref") != approval_ref
    ):
        raise ValueError("Creem sandbox report binding is invalid")
    facts = report.get("verified_facts")
    if (
        report.get("passed") is not True
        or not _SHA64.fullmatch(str(report.get("proof_id_hash") or ""))
        or report.get("request_count") != 1
        or not isinstance(report.get("replay_count"), int)
        or report["replay_count"] < 1
        or report.get("duplicate_effect_count") != 0
        or not isinstance(facts, dict)
        or set(facts) != required_facts
        or any(value is not True for value in facts.values())
    ):
        raise ValueError("Creem sandbox report does not prove the required behavior")
    produced_at = _timestamp(report.get("produced_at"), label="Creem produced_at")
    if produced_at > now + timedelta(minutes=5) or now - produced_at > timedelta(hours=2):
        raise ValueError("Creem sandbox report is stale or from the future")
    if len(signing_key) < 32:
        raise ValueError("Provider evidence signing key is missing or too short")
    match = _SIGNATURE.fullmatch(str(report.get("signature") or ""))
    unsigned = {key: value for key, value in report.items() if key != "signature"}
    wanted = hmac.new(
        signing_key,
        canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), wanted):
        raise ValueError("Creem sandbox report signature is invalid")
    return hashlib.sha256(canonical_json_bytes(report)).hexdigest()


def activate_creem_contracts(
    document: dict[str, Any],
    *,
    evidence_bundle: dict[str, Any],
    expected_tested_source_sha: str,
    signing_key: bytes,
    approval_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Activate all three Creem facts only from one complete genuine evidence bundle."""

    _validate_document(document)
    evolink = document["contracts"].get(EVOLINK_KEY)
    if not isinstance(evolink, dict) or evolink.get("state") != "VERIFIED":
        raise ValueError("Creem activation requires the prior VERIFIED Evolink contract")
    if (
        not isinstance(evidence_bundle, dict)
        or set(evidence_bundle) != {
            "schema",
            "official_contracts",
            "sandbox_reports",
        }
        or evidence_bundle.get("schema") != "vowpic.creem-activation-evidence.v1"
        or not isinstance(evidence_bundle.get("official_contracts"), dict)
        or not isinstance(evidence_bundle.get("sandbox_reports"), dict)
        or set(evidence_bundle["official_contracts"]) != set(CREEM_CONTRACTS)
        or set(evidence_bundle["sandbox_reports"]) != set(CREEM_CONTRACTS)
    ):
        raise ValueError("Creem activation evidence bundle is invalid")
    source_sha = str(expected_tested_source_sha or "").strip().lower()
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("Creem tested source SHA is invalid")
    if not str(approval_ref or "").strip():
        raise ValueError("Creem approval reference is required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    activated = copy.deepcopy(document)
    for name, contract_spec in CREEM_CONTRACTS.items():
        entry = activated["contracts"].get(name)
        if not isinstance(entry, dict) or entry.get("state") != "UNVERIFIED":
            raise ValueError(f"Creem contract is not UNVERIFIED: {name}")
        official = evidence_bundle["official_contracts"][name]
        official_hash = _validate_creem_official_contract(
            official,
            capability=contract_spec["capability"],
            now=current,
        )
        report = evidence_bundle["sandbox_reports"][name]
        report_hash = _validate_creem_sandbox_report(
            report,
            capability=contract_spec["capability"],
            required_facts=contract_spec["required_facts"],
            expected_tested_source_sha=source_sha,
            official_contract_sha256=official_hash,
            signing_key=signing_key,
            approval_ref=approval_ref,
            now=current,
        )
        entry.update(
            {
                "state": "VERIFIED",
                "official_source_url": official["official_source_url"],
                "official_version": official["official_version"],
                "official_contract_sha256": official_hash,
                "endpoint_schema_sha256": official["endpoint_schema_sha256"],
                "test_evidence_sha256": report_hash,
                "tested_source_sha": source_sha,
                "evidence_created_at": report["produced_at"],
                "approval_ref": approval_ref,
                "correlation_semantics": official["correlation_semantics"],
                "idempotency_semantics": official["idempotency_semantics"],
            }
        )
    return activated


def verify_activated_creem_evidence(
    document: dict[str, Any],
    *,
    evidence_bundle: dict[str, Any],
    expected_tested_source_sha: str,
    signing_key: bytes,
    now: datetime | None = None,
) -> dict[str, str]:
    """Revalidate genuine evidence against an already activated contract document."""

    _validate_document(document)
    approval_refs = {
        str(document["contracts"].get(name, {}).get("approval_ref") or "")
        for name in CREEM_CONTRACTS
    }
    if len(approval_refs) != 1 or not next(iter(approval_refs)):
        raise ValueError("activated Creem approval reference is inconsistent")
    baseline = copy.deepcopy(document)
    for name in CREEM_CONTRACTS:
        baseline["contracts"][name]["state"] = "UNVERIFIED"
    rebuilt = activate_creem_contracts(
        baseline,
        evidence_bundle=evidence_bundle,
        expected_tested_source_sha=expected_tested_source_sha,
        signing_key=signing_key,
        approval_ref=next(iter(approval_refs)),
        now=now,
    )
    hashes: dict[str, str] = {}
    for name in CREEM_CONTRACTS:
        expected = document["contracts"].get(name)
        actual = rebuilt["contracts"].get(name)
        if expected != actual:
            raise ValueError(f"Creem activated contract/evidence mismatch: {name}")
        hashes[name] = str(actual["test_evidence_sha256"])
    return hashes


def validate_activation_commit(
    *,
    tested_support_sha: str,
    activation_parent_sha: str,
    activation_source_sha: str,
    changed_paths: set[str],
) -> None:
    for label, value in (
        ("tested support SHA", tested_support_sha),
        ("activation parent SHA", activation_parent_sha),
        ("activation source SHA", activation_source_sha),
    ):
        if not _SHA40.fullmatch(str(value or "").strip().lower()):
            raise ValueError(f"{label} is invalid")
    if activation_parent_sha != tested_support_sha or activation_source_sha == tested_support_sha:
        raise ValueError("activation commit must directly follow the tested support commit")
    if set(changed_paths) != ALLOWED_ACTIVATION_PATHS:
        raise ValueError("activation commit must change only the contract and worklog")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    raw = canonical_json_bytes(payload) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout.strip().lower()


def validate_clean_worktree_status(status: str) -> None:
    if str(status or "").strip():
        raise ValueError("Provider contract activation requires a clean worktree")


def _require_clean_worktree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    validate_clean_worktree_status(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--expected-tested-source-sha", required=True)
    parser.add_argument("--evolink-report")
    parser.add_argument("--evolink-official-contract")
    parser.add_argument("--creem-evidence-bundle")
    parser.add_argument("--preserve-unverified", action="append", default=[])
    parser.add_argument("--approval-id-env", default="PROVIDER_CONTRACT_APPROVAL_ID")
    parser.add_argument("--signing-key-env", default="PROVIDER_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if _git_head() != args.expected_tested_source_sha.strip().lower():
            raise ValueError("tested support SHA is not the current checkout")
        _require_clean_worktree()
        document = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        if not args.evolink_report and not args.creem_evidence_bundle:
            raise ValueError("at least one Provider evidence report is required")
        for name in args.preserve_unverified:
            entry = document.get("contracts", {}).get(name)
            if not isinstance(entry, dict) or entry.get("state") != "UNVERIFIED":
                raise ValueError(f"preserved Provider contract is not UNVERIFIED: {name}")
        activated = document
        signing_key = os.environ.get(args.signing_key_env, "").encode("utf-8")
        approval_ref = os.environ.get(args.approval_id_env, "").strip()
        if args.evolink_report:
            report = json.loads(Path(args.evolink_report).read_text(encoding="utf-8"))
            if args.evolink_official_contract:
                official = json.loads(
                    Path(args.evolink_official_contract).read_text(encoding="utf-8")
                )
            else:
                official = report.pop("official_contract", None)
            if not isinstance(official, dict):
                raise ValueError("official Evolink contract evidence is required")
            activated = activate_evolink_contract(
                activated,
                official_contract=official,
                sandbox_report=report,
                expected_tested_source_sha=args.expected_tested_source_sha.strip().lower(),
                signing_key=signing_key,
                approval_ref=approval_ref,
            )
        if args.creem_evidence_bundle:
            bundle = json.loads(
                Path(args.creem_evidence_bundle).read_text(encoding="utf-8")
            )
            activated = activate_creem_contracts(
                activated,
                evidence_bundle=bundle,
                expected_tested_source_sha=args.expected_tested_source_sha.strip().lower(),
                signing_key=signing_key,
                approval_ref=approval_ref,
            )
        _write_json_atomic(Path(args.output), activated)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
