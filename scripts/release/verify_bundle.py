#!/usr/bin/env python3
"""Verify one immutable manifest and its fresh website-backend runtime report."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest


_SIGNATURE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _observed_at(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("API report observed_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("API report observed_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_api_report(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    report: dict[str, Any],
    signing_key: bytes,
    now: datetime | None = None,
    maximum_age: timedelta = timedelta(minutes=5),
) -> None:
    normalized = validate_manifest(manifest)
    expected_manifest_sha = hashlib.sha256(canonical_manifest_bytes(normalized)).hexdigest()
    if manifest_sha256 != expected_manifest_sha:
        raise ValueError("manifest SHA-256 does not match canonical bytes")
    expected = {
        "schema": "vowpic.api-runtime-report.v1",
        "manifest_sha256": manifest_sha256,
        "release_role": normalized["release_role"],
        "source_sha": normalized["source_sha"],
        "runtime_bundle_id": normalized["runtime_bundle_id"],
        "api_deployment_id": normalized["api_deployment_id"],
        "schema_revision": normalized["schema_revision"],
        "api_compatibility_version": normalized["api_compatibility_version"],
        "backend_execution_version": normalized["backend_execution_version"],
        "backend_executor_digest": normalized["backend_executor_digest"],
        "job_payload_min": normalized["job_payload_min"],
        "job_payload_max": normalized["job_payload_max"],
        "provider_policy_hash": normalized["contract_hashes"]["provider"],
        "flag_contract_hash": normalized["contract_hashes"]["flag"],
    }
    if not isinstance(report, dict) or any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("API runtime report does not match the immutable manifest")
    exact_fields = set(expected) | {
        "liveness_response_sha256",
        "readiness_response_sha256",
        "version_response_sha256",
        "observed_at",
        "signature",
    }
    if set(report) != exact_fields:
        raise ValueError("API runtime report contains unexpected or missing fields")
    for field in ("liveness_response_sha256", "readiness_response_sha256", "version_response_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(report.get(field) or "")):
            raise ValueError(f"API runtime report {field} is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current - _observed_at(report.get("observed_at"))
    if age < timedelta(minutes=-5) or age > maximum_age:
        raise ValueError("API runtime report is stale or from the future")
    if len(signing_key) < 32:
        raise ValueError("runtime report signing key is missing or too short")
    match = _SIGNATURE.fullmatch(str(report.get("signature") or ""))
    unsigned = {key: value for key, value in report.items() if key != "signature"}
    expected_signature = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), expected_signature):
        raise ValueError("API runtime report signature is invalid")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--api-report", required=True)
    parser.add_argument("--signing-key-env", default="RELEASE_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        api_report = json.loads(Path(args.api_report).read_text(encoding="utf-8"))
        verify_api_report(
            manifest,
            manifest_sha256=manifest_sha,
            report=api_report,
            signing_key=os.environ.get(args.signing_key_env, "").encode("utf-8"),
        )
        result = {
            "schema": "vowpic.bundle-verification.v1",
            "manifest_sha256": manifest_sha,
            "release_role": manifest["release_role"],
            "decision": "PASS",
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
