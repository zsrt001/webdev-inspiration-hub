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
    if EVOLINK_KEY not in document["contracts"]:
        raise ValueError("Evolink contract entry is missing")


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
    parser.add_argument("--evolink-report", required=True)
    parser.add_argument("--evolink-official-contract")
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
        report = json.loads(Path(args.evolink_report).read_text(encoding="utf-8"))
        if args.evolink_official_contract:
            official = json.loads(Path(args.evolink_official_contract).read_text(encoding="utf-8"))
        else:
            official = report.pop("official_contract", None)
        if not isinstance(official, dict):
            raise ValueError("official Evolink contract evidence is required")
        for name in args.preserve_unverified:
            entry = document.get("contracts", {}).get(name)
            if not isinstance(entry, dict) or entry.get("state") != "UNVERIFIED":
                raise ValueError(f"preserved Provider contract is not UNVERIFIED: {name}")
        activated = activate_evolink_contract(
            document,
            official_contract=official,
            sandbox_report=report,
            expected_tested_source_sha=args.expected_tested_source_sha.strip().lower(),
            signing_key=os.environ.get(args.signing_key_env, "").encode("utf-8"),
            approval_ref=os.environ.get(args.approval_id_env, "").strip(),
        )
        _write_json_atomic(Path(args.output), activated)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
