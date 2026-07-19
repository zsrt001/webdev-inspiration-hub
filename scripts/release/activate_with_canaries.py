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
import subprocess
import sys
from typing import Any


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
) -> dict[str, Any]:
    validate_plan(plan)
    if activation.get("phase") != "PUBLIC_INVALIDATED":
        raise ValueError("incremental activation requires PUBLIC_INVALIDATED")
    if not canary_contract.is_file() or canary_contract.resolve() != (
        ROOT / "frontend/e2e/production-canary.spec.ts"
    ).resolve():
        raise ValueError("production canary command contract is not pinned")
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
            **os.environ,
            "RUN_PRODUCTION_E2E": "1",
            "PRODUCTION_CANARY_CAPABILITY": capability,
            "PRODUCTION_CANARY_OUTPUT_PATH": str(report_path),
            "PRODUCTION_CANARY_BEFORE_SNAPSHOT_HASH": transition["old_snapshot_hash"],
            "PRODUCTION_CANARY_AFTER_SNAPSHOT_HASH": transition["new_snapshot_hash"],
            "PRODUCTION_SOURCE_SHA": activation["source_sha"],
            "PRODUCTION_RUNTIME_BUNDLE_ID": activation["runtime_bundle_id"],
            "PRODUCTION_DEPLOYMENT_ID": activation["api_deployment_id"],
            "PRODUCTION_MANIFEST_SHA256": activation["manifest_sha256"],
        }
        completed = subprocess.run(
            [
                "npm",
                "--prefix",
                "frontend",
                "run",
                "test:e2e",
                "--",
                "e2e/production-canary.spec.ts",
                "--grep",
                f"@capability:{capability}",
            ],
            cwd=ROOT,
            env=env,
            check=False,
            timeout=900,
        )
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
