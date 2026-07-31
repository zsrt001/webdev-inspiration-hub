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
from urllib.parse import urlsplit
from uuid import UUID


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
_PREDEPLOYMENT_CLEANED_KEYS = frozenset(
    {
        "activation_id",
        "api_deployment_id",
        "bindings_revoked",
        "business_rows",
        "consumed_users",
        "origin_cleanup",
        "origin_restored",
        "private_asset_prefix",
        "refresh_tokens_revoked",
        "runtime_bundle_id",
        "second_binding",
        "sessions_revoked",
        "source_sha",
        "state",
        "storage_objects_deleted",
        "storage_objects_remaining",
        "worker_deployment_id",
    }
)
_PREDEPLOYMENT_BUSINESS_KEYS = frozenset(
    {
        "account_risk_events",
        "credit_grant_lots",
        "credit_purchases",
        "credit_transactions",
        "live_portrait_jobs",
        "orders",
        "subscription_credit_grants",
        "user_credits",
        "user_subscriptions",
        "welcome_grant_claims",
    }
)
_FAILURE_STAGE_KEYS = frozenset(
    {
        "schema",
        "source_sha",
        "workflow_run_id",
        "workflow_attempt",
        "deploy_step_outcome",
        "deploy_attempted",
        "deployment_url",
        "deployment_url_recorded",
        "deployment_bound",
        "safe_predeployment_cleanup",
    }
)
_ORPHAN_DEPLOYMENT_CLEANUP_KEYS = frozenset(
    {
        "schema",
        "state",
        "source_sha",
        "workflow_run_id",
        "workflow_attempt",
        "deployment_id",
        "deployment_url",
        "project_id",
        "delete_status",
        "readback_status",
    }
)
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


def _required_uuid(report: dict[str, Any], field: str) -> str:
    value = _required_text(report, field)
    try:
        canonical = str(UUID(value))
    except ValueError as exc:
        raise PreviewCleanupGateError(
            f"cleanup report field {field} must be a canonical UUID"
        ) from exc
    if value != canonical:
        raise PreviewCleanupGateError(
            f"cleanup report field {field} must be a canonical UUID"
        )
    return canonical


def verify_predeployment_cleaned_report(
    report: Any,
    *,
    register_result: str,
) -> str:
    if register_result != "failure":
        raise PreviewCleanupGateError(
            "only a failed register job may clean a predeployment reservation"
        )
    if not isinstance(report, dict) or set(report) != _PREDEPLOYMENT_CLEANED_KEYS:
        raise PreviewCleanupGateError(
            "predeployment CLEANED cleanup schema is not exact"
        )
    if (
        report.get("state") != "CLEANED"
        or report.get("api_deployment_id") is not None
        or report.get("worker_deployment_id") is not None
        or report.get("origin_cleanup") != _NO_ACTIVATION_ORIGIN
        or report.get("origin_restored") is not False
        or report.get("second_binding") is not False
        or report.get("private_asset_prefix") != []
    ):
        raise PreviewCleanupGateError(
            "predeployment CLEANED cleanup contains unexpected runtime state"
        )
    for field in (
        "bindings_revoked",
        "consumed_users",
        "refresh_tokens_revoked",
        "sessions_revoked",
        "storage_objects_deleted",
        "storage_objects_remaining",
    ):
        if not _exact_zero(report.get(field)):
            raise PreviewCleanupGateError(
                f"predeployment CLEANED field {field} is not exact integer zero"
            )
    business_rows = report.get("business_rows")
    if (
        type(business_rows) is not dict
        or set(business_rows) != _PREDEPLOYMENT_BUSINESS_KEYS
        or any(not _exact_zero(value) for value in business_rows.values())
    ):
        raise PreviewCleanupGateError(
            "predeployment CLEANED cleanup contains unexpected business rows"
        )
    return _required_uuid(report, "activation_id")


def verify_safe_predeployment_stage(
    stage: Any,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
) -> dict[str, Any]:
    if not isinstance(stage, dict) or set(stage) != _FAILURE_STAGE_KEYS:
        raise PreviewCleanupGateError("Preview failure-stage schema is not exact")
    if stage.get("schema") != "vowpic.preview-deploy-stage.v1":
        raise PreviewCleanupGateError("Preview failure-stage schema is invalid")
    if (
        stage.get("source_sha") != source_sha
        or stage.get("workflow_run_id") != workflow_run_id
        or type(stage.get("workflow_attempt")) is not int
        or str(stage["workflow_attempt"]) != workflow_attempt
    ):
        raise PreviewCleanupGateError(
            "Preview failure-stage workflow coordinates do not match"
        )
    outcome = stage.get("deploy_step_outcome")
    if outcome not in {"success", "failure", "skipped"}:
        raise PreviewCleanupGateError("Preview failure-stage deploy outcome is invalid")
    for field in (
        "deploy_attempted",
        "deployment_url_recorded",
        "deployment_bound",
        "safe_predeployment_cleanup",
    ):
        if type(stage.get(field)) is not bool:
            raise PreviewCleanupGateError(
                f"Preview failure-stage field {field} must be boolean"
            )
    raw_url = stage.get("deployment_url")
    if raw_url is not None and type(raw_url) is not str:
        raise PreviewCleanupGateError(
            "Preview failure-stage deployment URL must be a string or null"
        )
    deployment_url = str(raw_url or "").strip()
    if bool(deployment_url) is not stage["deployment_url_recorded"]:
        raise PreviewCleanupGateError(
            "Preview failure-stage deployment URL record is inconsistent"
        )
    if deployment_url:
        parsed = urlsplit(deployment_url)
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".vercel.app")
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise PreviewCleanupGateError(
                "Preview failure-stage deployment URL is invalid"
            )

    no_deployment = (
        outcome in {"failure", "skipped"}
        and not stage["deploy_attempted"]
        and not stage["deployment_url_recorded"]
        and not stage["deployment_bound"]
        and stage["safe_predeployment_cleanup"]
    )
    unbound_deployment = (
        outcome == "success"
        and stage["deploy_attempted"]
        and stage["deployment_url_recorded"]
        and not stage["deployment_bound"]
        and not stage["safe_predeployment_cleanup"]
    )
    if not (no_deployment or unbound_deployment):
        raise PreviewCleanupGateError(
            "Preview failure-stage does not prove a safe cleanup boundary"
        )
    return stage


def verify_orphan_deployment_cleanup(
    cleanup: Any,
    *,
    stage: dict[str, Any],
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
) -> dict[str, Any]:
    if not isinstance(cleanup, dict) or set(cleanup) != _ORPHAN_DEPLOYMENT_CLEANUP_KEYS:
        raise PreviewCleanupGateError(
            "orphan Preview deployment cleanup schema is not exact"
        )
    common_proof_is_invalid = (
        cleanup.get("schema") != "vowpic.preview-orphan-deployment-cleanup.v1"
        or cleanup.get("source_sha") != source_sha
        or cleanup.get("workflow_run_id") != workflow_run_id
        or type(cleanup.get("workflow_attempt")) is not int
        or str(cleanup["workflow_attempt"]) != workflow_attempt
        or cleanup.get("deployment_url") != stage.get("deployment_url")
        or cleanup.get("readback_status") != 404
    )
    deleted = (
        cleanup.get("state") == "DELETED"
        and cleanup.get("delete_status") in {200, 204}
        and re.fullmatch(
            r"dpl_[A-Za-z0-9]+", str(cleanup.get("deployment_id") or "")
        )
        is not None
    )
    already_absent = (
        cleanup.get("state") == "ALREADY_ABSENT"
        and cleanup.get("deployment_id") is None
        and cleanup.get("delete_status") == 404
    )
    if common_proof_is_invalid or not (deleted or already_absent):
        raise PreviewCleanupGateError(
            "orphan Preview deployment cleanup does not prove exact deletion"
        )
    if not re.fullmatch(r"prj_[A-Za-z0-9]+", str(cleanup.get("project_id") or "")):
        raise PreviewCleanupGateError(
            "orphan Preview deployment cleanup has an invalid project ID"
        )
    return cleanup


def materialize_cleanup_gate(
    report: Any,
    *,
    failure_stage: Any | None = None,
    orphan_deployment_cleanup: Any | None = None,
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
        stage = verify_safe_predeployment_stage(
            failure_stage,
            source_sha=source_sha,
            workflow_run_id=workflow_run_id,
            workflow_attempt=workflow_attempt,
        )
        if stage.get("deployment_url") is not None:
            raise PreviewCleanupGateError(
                "NO_ACTIVATION cleanup cannot leave an unbound deployment"
            )
        return None
    if not isinstance(report, dict) or report.get("state") != "CLEANED":
        raise PreviewCleanupGateError("cleanup report is neither CLEANED nor NO_ACTIVATION")
    if report.get("source_sha") != source_sha:
        raise PreviewCleanupGateError(
            "cleanup report does not prove the exact source activation"
        )
    runtime_bundle_id = _required_text(report, "runtime_bundle_id")
    raw_api_deployment_id = report.get("api_deployment_id")
    if raw_api_deployment_id is None:
        activation_id = verify_predeployment_cleaned_report(
            report,
            register_result=register_result,
        )
        stage = verify_safe_predeployment_stage(
            failure_stage,
            source_sha=source_sha,
            workflow_run_id=workflow_run_id,
            workflow_attempt=workflow_attempt,
        )
        if stage.get("deployment_url") is not None:
            verify_orphan_deployment_cleanup(
                orphan_deployment_cleanup,
                stage=stage,
                source_sha=source_sha,
                workflow_run_id=workflow_run_id,
                workflow_attempt=workflow_attempt,
            )
        cleanup_coordinate = f"activation-{activation_id}"
    else:
        cleanup_coordinate = _required_text(report, "api_deployment_id")
    if not gate_contract.is_file():
        raise PreviewCleanupGateError("gate contract is missing")

    root = (
        release_root
        / source_sha
        / f"{workflow_run_id}-{workflow_attempt}"
        / cleanup_coordinate
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
    parser.add_argument("--failure-stage", type=Path)
    parser.add_argument("--orphan-deployment-cleanup", type=Path)
    parser.add_argument("--register-result", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True)
    parser.add_argument("--gate-contract", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.cleanup_report.read_text(encoding="utf-8"))
        failure_stage = (
            json.loads(args.failure_stage.read_text(encoding="utf-8"))
            if args.failure_stage is not None and args.failure_stage.is_file()
            else None
        )
        orphan_deployment_cleanup = (
            json.loads(args.orphan_deployment_cleanup.read_text(encoding="utf-8"))
            if args.orphan_deployment_cleanup is not None
            and args.orphan_deployment_cleanup.is_file()
            else None
        )
        output = materialize_cleanup_gate(
            report,
            failure_stage=failure_stage,
            orphan_deployment_cleanup=orphan_deployment_cleanup,
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
