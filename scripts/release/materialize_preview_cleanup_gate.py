#!/usr/bin/env python3
"""Verify Preview cleanup and materialize gate evidence only for a real activation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


class PreviewCleanupGateError(RuntimeError):
    pass


_NO_ACTIVATION_KEYS = frozenset(
    {
        "state",
        "bindings_revoked",
        "sessions_revoked",
        "refresh_tokens_revoked",
        "business_rows",
        "origin_cleanup",
    }
)
_NO_ACTIVATION_ORIGIN = {
    "origin_state_artifact": "ABSENT",
    "state": "NOT_REQUIRED",
}
_REGISTER_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


def _exact_zero(value: Any) -> bool:
    return type(value) is int and value == 0


def verify_no_activation_report(
    report: Any,
    *,
    register_result: str,
) -> dict[str, Any]:
    result = str(register_result or "").strip()
    if result not in _REGISTER_RESULTS:
        raise PreviewCleanupGateError("register result is not a recognized job result")
    if result == "success":
        raise PreviewCleanupGateError(
            "successful Preview registration cannot end with NO_ACTIVATION cleanup"
        )
    if not isinstance(report, dict) or set(report) != _NO_ACTIVATION_KEYS:
        raise PreviewCleanupGateError("NO_ACTIVATION cleanup schema is not exact")
    if report.get("state") != "NO_ACTIVATION":
        raise PreviewCleanupGateError("cleanup report is not NO_ACTIVATION")
    for field in (
        "bindings_revoked",
        "sessions_revoked",
        "refresh_tokens_revoked",
    ):
        if not _exact_zero(report.get(field)):
            raise PreviewCleanupGateError(
                f"NO_ACTIVATION cleanup field {field} is not exact integer zero"
            )
    if type(report.get("business_rows")) is not dict or report["business_rows"]:
        raise PreviewCleanupGateError(
            "NO_ACTIVATION cleanup contains unexpected business rows"
        )
    origin = report.get("origin_cleanup")
    if type(origin) is not dict or origin != _NO_ACTIVATION_ORIGIN:
        raise PreviewCleanupGateError(
            "NO_ACTIVATION cleanup contains unexpected origin side effects"
        )
    return {
        "state": "NO_ACTIVATION_VERIFIED",
        "register_result": result,
        "side_effects": 0,
    }


def _required_text(report: dict[str, Any], field: str) -> str:
    value = report.get(field)
    if type(value) is not str or not value.strip():
        raise PreviewCleanupGateError(
            f"cleanup report field {field} must be a non-empty string"
        )
    return value.strip()


def materialize_cleanup_gate(
    report: Any,
    *,
    register_result: str,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
    gate_contract: Path,
    release_root: Path,
) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise PreviewCleanupGateError("source SHA must be canonical")
    if not str(workflow_run_id).isdigit() or not str(workflow_attempt).isdigit():
        raise PreviewCleanupGateError("workflow coordinates must be numeric")
    if isinstance(report, dict) and report.get("state") == "NO_ACTIVATION":
        verify_no_activation_report(report, register_result=register_result)
        return None
    if not isinstance(report, dict) or report.get("state") != "CLEANED":
        raise PreviewCleanupGateError("cleanup report is neither CLEANED nor NO_ACTIVATION")
    if report.get("source_sha") != source_sha:
        raise PreviewCleanupGateError(
            "cleanup report does not prove the exact source activation"
        )
    runtime_bundle_id = _required_text(report, "runtime_bundle_id")
    api_deployment_id = _required_text(report, "api_deployment_id")
    if not gate_contract.is_file():
        raise PreviewCleanupGateError("gate contract is missing")

    root = (
        release_root
        / source_sha
        / f"{workflow_run_id}-{workflow_attempt}"
        / api_deployment_id
        / "08-cleanup"
    )
    root.mkdir(parents=True, exist_ok=False)
    payload = {
        "schema": "vowpic.gate-evidence.v1",
        "case_id": "preview_cleanup",
        "status": "PASS",
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "gate_contract_sha256": hashlib.sha256(gate_contract.read_bytes()).hexdigest(),
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "execution": "completed",
        "test_count": 1,
        "capability_enabled": False,
    }
    output = root / "preview_cleanup.json"
    output.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup-report", type=Path, required=True)
    parser.add_argument("--register-result", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True)
    parser.add_argument("--gate-contract", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.cleanup_report.read_text(encoding="utf-8"))
        output = materialize_cleanup_gate(
            report,
            register_result=args.register_result,
            source_sha=args.source_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            gate_contract=args.gate_contract,
            release_root=args.release_root,
        )
        state = "CLEANED_EVIDENCE_WRITTEN" if output is not None else "NO_ACTIVATION_VERIFIED"
        print(json.dumps({"state": state}, sort_keys=True))
        return 0
    except (OSError, ValueError, PreviewCleanupGateError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
