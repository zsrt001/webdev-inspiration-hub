#!/usr/bin/env python3
"""Verify authenticated, release-bound edge-lockdown evidence before deploy access."""

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
from urllib.parse import urlsplit


NOT_RUN_EXIT = 3
MAX_REPORT_LIFETIME = timedelta(hours=1)
MIN_REPORT_REMAINING = timedelta(minutes=15)
EDGE_ROUTE_GROUPS = {
    "auth_upload",
    "generation",
    "credit_checkout",
    "subscription",
    "partner_invite",
    "retired_addons",
    "leads_recommendations",
}
# The current Vercel Hobby project permits three custom rules. One slot is
# reserved for the short-lived runner bypass, leaving at most two physical
# deny rules. Logical route groups may intentionally share a physical rule ID.
MAX_PHYSICAL_EDGE_DENY_RULES = 2


def _parse_datetime(value: Any, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"edge lockdown {name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"edge lockdown {name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _domain_host(value: str) -> str:
    clean = str(value or "").strip().lower()
    parsed = urlsplit(clean if "://" in clean else f"https://{clean}")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError("formal domain must be one HTTPS hostname without a path")
    return parsed.hostname


def _canonical_unsigned_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature_hmac_sha256"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def compute_report_hmac(payload: dict[str, Any], hmac_key: bytes) -> str:
    if len(hmac_key) < 32:
        raise ValueError("edge evidence HMAC key must contain at least 32 bytes")
    return hmac.new(hmac_key, _canonical_unsigned_payload(payload), hashlib.sha256).hexdigest()


def _reject_sensitive_fields(value: Any, *, path: str = "report") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in ("token", "password", "secret", "hook_url")):
                raise ValueError(f"edge lockdown evidence contains forbidden sensitive field: {path}.{key}")
            _reject_sensitive_fields(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_fields(item, path=f"{path}[{index}]")


def _bounded_identifier(value: Any, *, name: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 200 or any(char.isspace() for char in clean):
        raise ValueError(f"edge lockdown {name} is invalid")
    return clean


def validate_edge_lockdown_report(
    payload: dict[str, Any],
    *,
    hmac_key: bytes,
    expected_source_sha: str,
    expected_workflow_run_id: str,
    expected_workflow_attempt: int,
    expected_project_id: str,
    expected_formal_domain: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("edge lockdown report must be a JSON object")
    _reject_sensitive_fields(payload)
    signature = str(payload.get("signature_hmac_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ValueError("edge lockdown report signature is missing or invalid")
    if not hmac.compare_digest(signature, compute_report_hmac(payload, hmac_key)):
        raise ValueError("edge lockdown report signature mismatch")
    if payload.get("schema_version") != "vowpic.edge-lockdown.v1" or payload.get("passed") is not True:
        raise ValueError("edge lockdown report is not a PASS v1 report")

    expected_coordinates = {
        "source_sha": str(expected_source_sha).strip().lower(),
        "workflow_run_id": str(expected_workflow_run_id).strip(),
        "workflow_attempt": expected_workflow_attempt,
        "project_id": str(expected_project_id).strip(),
        "formal_domain": _domain_host(expected_formal_domain),
    }
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_coordinates["source_sha"]):
        raise ValueError("expected source SHA is invalid")
    for name in ("workflow_run_id", "project_id"):
        _bounded_identifier(expected_coordinates[name], name=f"expected {name}")
    for name, expected in expected_coordinates.items():
        actual: Any = payload.get(name)
        if name == "workflow_attempt":
            if type(actual) is not int or type(expected) is not int or expected < 1:
                raise ValueError("edge lockdown workflow_attempt mismatch")
        else:
            actual = str(actual or "").strip()
        if name in {"source_sha", "formal_domain"}:
            actual = actual.lower()
        if actual != expected:
            raise ValueError(f"edge lockdown {name} mismatch")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = _parse_datetime(payload.get("generated_at"), name="generated_at")
    expires_at = _parse_datetime(payload.get("expires_at"), name="expires_at")
    if generated_at > current + timedelta(minutes=5):
        raise ValueError("edge lockdown report is dated in the future")
    if expires_at - current < MIN_REPORT_REMAINING:
        raise ValueError("edge lockdown report has less than fifteen minutes remaining")
    if expires_at <= generated_at or expires_at - generated_at > MAX_REPORT_LIFETIME:
        raise ValueError("edge lockdown report lifetime exceeds one hour")
    if payload.get("auto_assign_custom_production_domains") is not False:
        raise ValueError("Vercel Production domain auto-assignment is not disabled")
    if int(payload.get("active_deploy_hook_count", -1)) != 0:
        raise ValueError("active Vercel deploy hooks remain")

    for field in ("before_config_sha256", "after_config_sha256", "baseline_config_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get(field) or "")):
            raise ValueError(f"edge lockdown {field} is invalid")
    _bounded_identifier(payload.get("last_known_deployment_id"), name="last-known deployment ID")

    groups = payload.get("route_groups")
    if not isinstance(groups, dict) or set(groups) != EDGE_ROUTE_GROUPS:
        raise ValueError("edge lockdown route-group coverage is incomplete")
    physical_deny_rule_ids: set[str] = set()
    for name, result in groups.items():
        if not isinstance(result, dict):
            raise ValueError(f"edge lockdown route group {name} is invalid")
        physical_deny_rule_ids.add(
            _bounded_identifier(result.get("rule_id"), name=f"{name} rule ID")
        )
        if result.get("denied") is not True or result.get("read_back") is not True:
            raise ValueError(f"edge lockdown route group {name} was not denied and read back")
    if len(physical_deny_rule_ids) > MAX_PHYSICAL_EDGE_DENY_RULES:
        raise ValueError(
            "edge lockdown exceeds the Hobby physical-rule limit reserved for deny rules"
        )

    bypass = payload.get("runner_bypass")
    if not isinstance(bypass, dict) or bypass.get("read_back") is not True:
        raise ValueError("temporary runner bypass was not read back")
    _bounded_identifier(bypass.get("rule_id"), name="runner bypass rule ID")
    bypass_expiry = _parse_datetime(
        bypass.get("lease_expires_at"),
        name="runner bypass lease expiry",
    )
    if bypass_expiry <= current or bypass_expiry > expires_at:
        raise ValueError("temporary runner bypass lease is outside the report lease")

    return {
        "passed": True,
        "schema_version": "vowpic.edge-lockdown-verification.v1",
        "source_sha": expected_coordinates["source_sha"],
        "workflow_run_id": expected_coordinates["workflow_run_id"],
        "workflow_attempt": expected_coordinates["workflow_attempt"],
        "project_id": expected_coordinates["project_id"],
        "formal_domain": expected_coordinates["formal_domain"],
        "last_known_deployment_id": payload["last_known_deployment_id"],
        "route_groups": sorted(groups),
        "physical_deny_rule_count": len(physical_deny_rule_ids),
        "before_config_sha256": payload["before_config_sha256"],
        "after_config_sha256": payload["after_config_sha256"],
        "baseline_config_sha256": payload["baseline_config_sha256"],
        "report_expires_at": expires_at.isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-workflow-run-id", required=True)
    parser.add_argument("--expected-workflow-attempt", required=True, type=int)
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--expected-formal-domain", required=True)
    parser.add_argument("--hmac-key-env", default="EDGE_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
    report_path = Path(args.report)
    if not key or not report_path.is_file():
        print("NOT_RUN: authenticated edge-lockdown report and HMAC key are required", file=sys.stderr)
        return NOT_RUN_EXIT
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        verified = validate_edge_lockdown_report(
            payload,
            hmac_key=key,
            expected_source_sha=args.expected_source_sha,
            expected_workflow_run_id=args.expected_workflow_run_id,
            expected_workflow_attempt=args.expected_workflow_attempt,
            expected_project_id=args.expected_project_id,
            expected_formal_domain=args.expected_formal_domain,
        )
        verified["input_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        _write_create_once(Path(args.output), verified)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": True, "output": args.output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
