#!/usr/bin/env python3
"""Turn on each COMMERCIAL_7A capability only after its own signed canary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.apply_activation_plan import (  # noqa: E402
    CAPABILITIES,
    _activation,
    _canonical,
    _database_url,
    _load_object,
    transition_one_capability,
    validate_plan,
)
from scripts.release.run_account_cleanup_verification import (  # noqa: E402
    _base_url,
    _post_json,
)


SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _validate_canary(
    report: dict[str, Any],
    *,
    capability: str,
    activation: dict[str, Any],
    transition: dict[str, Any],
    signing_key: bytes,
) -> None:
    signature = str(report.get("signature") or "")
    if len(signing_key) < 32 or not signature.startswith("hmac-sha256:"):
        raise ValueError("production canary signature is invalid")
    unsigned = dict(report)
    unsigned.pop("signature", None)
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted):
        raise ValueError("production canary signature mismatch")
    expected = {
        "schema": "vowpic.production-capability-canary.v1",
        "passed": True,
        "capability": capability,
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
        "before_snapshot_hash": transition["old_snapshot_hash"],
        "after_snapshot_hash": transition["new_snapshot_hash"],
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("production canary release binding mismatch")


def _validate_cleanup_report(
    report: dict[str, Any],
    *,
    activation: dict[str, Any],
    signing_key: bytes,
) -> dict[str, Any]:
    signature = str(report.get("signature") or "")
    if len(signing_key) < 32 or not signature.startswith("hmac-sha256:"):
        raise ValueError("production canary cleanup signature is invalid")
    unsigned = dict(report)
    unsigned.pop("signature", None)
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted):
        raise ValueError("production canary cleanup signature mismatch")
    expected = {
        "schema": "vowpic.production-canary-cleanup.v1",
        "passed": True,
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
    }
    if any(unsigned.get(key) != value for key, value in expected.items()):
        raise ValueError("production canary cleanup release binding mismatch")
    user_id = str(UUID(str(unsigned.get("user_id") or "")))
    user_subject_hmac = str(unsigned.get("user_subject_hmac_sha256") or "")
    observations = unsigned.get("observations")
    if (
        not SHA64.fullmatch(user_subject_hmac)
        or observations
        != {
            "account_closed": True,
            "media_cleanup_requested": True,
            "post_close_session_denied": True,
        }
    ):
        raise ValueError("production canary cleanup observations are invalid")
    return {**unsigned, "user_id": user_id}


def _verify_cleanup_absence(
    *,
    base_url: str,
    cleanup_report: dict[str, Any],
    cron_token: str,
    attempts: int = 30,
) -> dict[str, Any]:
    if len(cron_token.encode("utf-8")) < 24:
        raise ValueError("production canary cleanup token is missing or too short")
    request = {
        "user_id": cleanup_report["user_id"],
        "source_sha": cleanup_report["source_sha"],
        "runtime_bundle_id": cleanup_report["runtime_bundle_id"],
        "deployment_id": cleanup_report["deployment_id"],
        "manifest_sha256": cleanup_report["manifest_sha256"],
    }
    absence: dict[str, Any] | None = None
    iteration = 0
    for iteration in range(1, max(1, min(30, attempts)) + 1):
        cleanup_status, cleanup = _post_json(
            f"{_base_url(base_url)}/api/v1/ops/cleanup_expired_assets",
            token=cron_token,
            payload={},
        )
        if cleanup_status != 200 or cleanup.get("success") is not True:
            raise ValueError("production canary cleanup did not report success")
        absence_status, candidate = _post_json(
            f"{_base_url(base_url)}/api/v1/ops/verify_acceptance_media_absence",
            token=cron_token,
            payload=request,
            allow_conflict=True,
        )
        if absence_status == 200:
            absence = candidate
            break
        if iteration < attempts:
            time.sleep(2.0)
    if absence is None:
        raise ValueError("production canary media absence was not proven")
    expected = {
        "schema": "vowpic.acceptance-media-absence.v1",
        "passed": True,
        "source_sha": cleanup_report["source_sha"],
        "runtime_bundle_id": cleanup_report["runtime_bundle_id"],
        "deployment_id": cleanup_report["deployment_id"],
        "manifest_sha256": cleanup_report["manifest_sha256"],
        "user_subject_hmac_sha256": cleanup_report["user_subject_hmac_sha256"],
        "storage_read_outcome": "NOT_FOUND",
    }
    if (
        any(absence.get(key) != value for key, value in expected.items())
        or not isinstance(absence.get("verified_asset_count"), int)
        or int(absence["verified_asset_count"]) < 1
        or not SHA64.fullmatch(str(absence.get("facts_sha256") or ""))
    ):
        raise ValueError("production canary media absence response is invalid")
    return {
        "passed": True,
        "user_subject_hmac_sha256": cleanup_report["user_subject_hmac_sha256"],
        "verified_asset_count": absence["verified_asset_count"],
        "storage_read_outcome": "NOT_FOUND",
        "facts_sha256": absence["facts_sha256"],
        "cleanup_iterations": iteration,
    }


def _run_playwright(*, env: dict[str, str], grep: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "npm",
            "--prefix",
            "frontend",
            "run",
            "test:e2e",
            "--",
            "e2e/production-canary.spec.ts",
            "--grep",
            grep,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        timeout=900,
    )


def _activation_row(
    database_url: str, *, deployment_id: str, source_sha: str, approval: str
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            return _activation(
                cursor,
                deployment_id=deployment_id,
                source_sha=source_sha,
                approval=approval,
                for_update=False,
            )


def activate(
    *,
    database_url: str,
    activation: dict[str, Any],
    plan: dict[str, Any],
    canary_contract: Path,
    approval: str,
    signing_key: bytes,
    runner_temp: Path,
    base_url: str,
    cleanup_cron_token: str,
) -> dict[str, Any]:
    validate_plan(plan)
    if activation.get("phase") != "PUBLIC_INVALIDATED":
        raise ValueError("incremental activation requires PUBLIC_INVALIDATED")
    if not canary_contract.is_file() or canary_contract.resolve() != (
        ROOT / "frontend/e2e/production-canary.spec.ts"
    ).resolve():
        raise ValueError("production canary command contract is not pinned")
    runner_temp.mkdir(parents=True, exist_ok=True)
    state_path = runner_temp / "production-canary-runtime-state.json"
    cleanup_path = runner_temp / "production-canary-cleanup.json"
    shared_env = {
        **os.environ,
        "RUN_PRODUCTION_E2E": "1",
        "PRODUCTION_CANARY_STATE_PATH": str(state_path),
        "PRODUCTION_CANARY_CLEANUP_OUTPUT_PATH": str(cleanup_path),
        "PRODUCTION_SOURCE_SHA": activation["source_sha"],
        "PRODUCTION_RUNTIME_BUNDLE_ID": activation["runtime_bundle_id"],
        "PRODUCTION_DEPLOYMENT_ID": activation["api_deployment_id"],
        "PRODUCTION_MANIFEST_SHA256": activation["manifest_sha256"],
    }
    events: list[dict[str, Any]] = []
    for capability in plan["flag_order"]:
        transition = transition_one_capability(
            database_url,
            activation_id=str(activation["id"]),
            capability=capability,
            expected_state="ACCEPTANCE_COHORT",
            target_state="ON",
            approval=approval,
            reason=f"COMMERCIAL_7A enable {capability} before exact canary",
        )
        report_path = runner_temp / f"production-canary-{capability}.json"
        env = {
            **shared_env,
            "PRODUCTION_CANARY_CAPABILITY": capability,
            "PRODUCTION_CANARY_OUTPUT_PATH": str(report_path),
            "PRODUCTION_CANARY_BEFORE_SNAPSHOT_HASH": transition["old_snapshot_hash"],
            "PRODUCTION_CANARY_AFTER_SNAPSHOT_HASH": transition["new_snapshot_hash"],
        }
        completed = _run_playwright(env=env, grep=f"@capability:{capability}")
        try:
            if completed.returncode != 0 or not report_path.is_file():
                raise ValueError(f"production canary failed: {capability}")
            report = _load_object(report_path)
            _validate_canary(
                report,
                capability=capability,
                activation=activation,
                transition=transition,
                signing_key=signing_key,
            )
        except Exception:
            transition_one_capability(
                database_url,
                activation_id=str(activation["id"]),
                capability=capability,
                expected_state="ON",
                target_state="OFF",
                approval=approval,
                reason=f"COMMERCIAL_7A canary failed; disable {capability}",
            )
            raise
        events.append(
            {
                "capability": capability,
                "before_snapshot_hash": transition["old_snapshot_hash"],
                "after_snapshot_hash": transition["new_snapshot_hash"],
                "canary_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                "passed": True,
            }
        )
    cleanup_completed = _run_playwright(env=shared_env, grep="@cleanup")
    if cleanup_completed.returncode != 0 or not cleanup_path.is_file():
        raise ValueError("production canary account cleanup failed")
    cleanup_report = _validate_cleanup_report(
        _load_object(cleanup_path),
        activation=activation,
        signing_key=signing_key,
    )
    cleanup_absence = _verify_cleanup_absence(
        base_url=base_url,
        cleanup_report=cleanup_report,
        cron_token=cleanup_cron_token,
    )
    capability_events_sha256 = hashlib.sha256(_canonical(events)).hexdigest()
    return {
        "schema": "vowpic.incremental-activation-report.v1",
        "passed": True,
        "release_role": "COMMERCIAL_7A",
        "phase": "formal-on",
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
        "capability_events": events,
        "canary_cleanup": {
            **cleanup_absence,
            "cleanup_report_sha256": hashlib.sha256(cleanup_path.read_bytes()).hexdigest(),
        },
        "target_snapshot_hash": activation["target_snapshot_hash"],
        "capability_events_sha256": capability_events_sha256,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-role", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--phase", required=True, choices=("formal-on",))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--activation-plan", required=True)
    parser.add_argument("--required-invalidation-report", required=True)
    parser.add_argument("--canary-command-contract", required=True)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--signing-key-env", default="ACCEPTANCE_EVIDENCE_SIGNING_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        approval = os.environ.get(args.approval_id_env, "").strip()
        signing_key = os.environ.get(args.signing_key_env, "").encode("utf-8")
        if not approval or len(signing_key) < 32:
            raise ValueError("incremental activation approval and signing key are required")
        manifest_path = Path(args.manifest)
        manifest = _load_object(manifest_path)
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if (
            manifest.get("schema") != "vowpic.bundle-manifest.v1"
            or manifest.get("release_role") != "COMMERCIAL_7A"
            or manifest.get("staged_target_deployment_id") != args.deployment_id
        ):
            raise ValueError("incremental activation manifest is invalid")
        invalidation = _load_object(args.required_invalidation_report)
        if invalidation.get("passed") is not True:
            raise ValueError("public media invalidation did not pass")
        database_url = os.environ.get(args.database_url_env, "")
        activation = _activation_row(
            database_url,
            deployment_id=args.deployment_id,
            source_sha=manifest["source_sha"],
            approval=approval,
        )
        if activation.get("manifest_sha256") != manifest_sha:
            raise ValueError("incremental activation manifest hash mismatch")
        report = activate(
            database_url=database_url,
            activation=activation,
            plan=_load_object(args.activation_plan),
            canary_contract=Path(args.canary_command_contract),
            approval=approval,
            signing_key=signing_key,
            runner_temp=Path(os.environ.get("RUNNER_TEMP") or ROOT / ".tmp/activation"),
            base_url=os.environ.get("PRODUCTION_BASE_URL", ""),
            cleanup_cron_token=os.environ.get("CLEANUP_CRON_TOKEN", ""),
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
