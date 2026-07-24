#!/usr/bin/env python3
"""Record an observation rollback only after the exact baseline is verified."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
for location in (ROOT, ROOT / "backend"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from backend.scripts.rollback_baseline_evidence import (  # noqa: E402
    _verify_signed_runtime,
)
from scripts.release.observe_release import (  # noqa: E402
    _aware,
    _bounded_report,
    _canonical,
    _database_url,
    _load_run,
)
from scripts.release.private_evidence_store import (  # noqa: E402
    PrivateBlobEvidenceStore,
)


def _validate_inputs(args: argparse.Namespace):
    resolution, resolution_sha = _bounded_report(
        args.resolution_report,
        label="observation recovery resolution",
    )
    baseline, baseline_sha = _bounded_report(
        args.baseline_resolution,
        label="rollback baseline resolution",
    )
    off_report, off_report_sha = _bounded_report(
        args.off_report,
        label="observation recovery OFF report",
    )
    api, api_sha = _bounded_report(
        args.api_report,
        label="observation recovery API report",
    )
    if (
        resolution.get("schema")
        != "vowpic.observation-recovery-resolution.v1"
        or resolution.get("passed") is not True
        or resolution.get("release_role") != "COMMERCIAL_7A"
        or baseline.get("schema")
        != "vowpic.rollback-baseline-resolution.v1"
        or baseline.get("passed") is not True
        or baseline.get("target_source_sha") != resolution.get("source_sha")
        or baseline.get("target_runtime_bundle_id")
        != resolution.get("runtime_bundle_id")
        or baseline.get("target_deployment_id")
        != resolution.get("api_deployment_id")
        or baseline.get("baseline_deployment_id")
        != resolution.get("private_compatible_baseline_deployment_id")
        or baseline.get("baseline_deployment_url")
        != resolution.get("private_compatible_baseline_url")
        or args.disposition != "ROLLED_BACK_PRIVATE_BASELINE"
    ):
        raise ValueError("observation rollback resolution is invalid")
    if (
        off_report.get("schema") != "vowpic.activation-plan-report.v1"
        or off_report.get("passed") is not True
        or off_report.get("phase") != "emergency-off"
        or off_report.get("source_sha") != resolution["source_sha"]
        or off_report.get("runtime_bundle_id") != resolution["runtime_bundle_id"]
        or off_report.get("deployment_id") != resolution["api_deployment_id"]
        or not isinstance(off_report.get("target_states"), dict)
        or not off_report["target_states"]
        or set(off_report["target_states"].values()) != {"OFF"}
    ):
        raise ValueError("observation recovery OFF report is invalid")
    api = _verify_signed_runtime(
        api,
        signing_key=os.environ.get(args.api_signing_key_env, "").encode(),
    )
    expected_api = {
        "source_sha": baseline["baseline_source_sha"],
        "runtime_bundle_id": baseline["baseline_runtime_bundle_id"],
        "api_deployment_id": baseline["baseline_deployment_id"],
        "schema_revision": baseline["baseline_schema_revision"],
        "release_role": baseline["baseline_release_role"],
    }
    if any(api.get(key) != value for key, value in expected_api.items()):
        raise ValueError("observation rollback API does not match the baseline")
    return (
        resolution,
        resolution_sha,
        baseline,
        baseline_sha,
        off_report,
        off_report_sha,
        api,
        api_sha,
    )


def complete(args: argparse.Namespace) -> dict:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    (
        resolution,
        resolution_sha,
        baseline,
        baseline_sha,
        off_report,
        off_report_sha,
        api,
        api_sha,
    ) = _validate_inputs(args)
    approval = os.environ.get(args.approval_id_env, "").strip()
    if not approval:
        raise ValueError("observation recovery approval is missing")
    approval_sha = hashlib.sha256(approval.encode()).hexdigest()
    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    database_url = os.environ.get(args.database_url_env, "")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("vowpic-observation",),
            )
            run = _load_run(
                cursor,
                resolution["observation_run_id"],
                lock=True,
            )
            if (
                run["state"] != "FAILED"
                or run["source_sha"] != resolution["source_sha"]
                or run["runtime_bundle_id"] != resolution["runtime_bundle_id"]
                or run["api_deployment_id"] != resolution["api_deployment_id"]
                or run["approval"] != approval
                or str(off_report.get("activation_id"))
                != str(run["release_activation_id"])
                or off_report.get("manifest_sha256")
                != run["manifest_sha256"]
            ):
                raise ValueError("observation recovery database coordinates drifted")
            cursor.execute(
                "SELECT * FROM release_observation_recoveries "
                "WHERE observation_run_id=%s FOR UPDATE",
                (run["id"],),
            )
            existing = cursor.fetchone()
            prefix = str(run["private_evidence_prefix"]).strip("/\\")
            object_key = f"{prefix}/observations/{run['id']}/recovery.json"
            if existing is not None:
                raw = store.read(object_key)
                if (
                    hashlib.sha256(raw).hexdigest()
                    != existing["recovery_report_sha256"]
                    or existing["private_object_key"] != object_key
                ):
                    raise ValueError("recorded observation recovery evidence drifted")
                return json.loads(raw)
            report = {
                "schema": "vowpic.observation-recovery-complete.v2",
                "passed": True,
                "state": "RECORDED",
                "observation_run_id": resolution["observation_run_id"],
                "target_source_sha": resolution["source_sha"],
                "target_runtime_bundle_id": resolution["runtime_bundle_id"],
                "rolled_back_api_deployment_id": baseline[
                    "baseline_deployment_id"
                ],
                "rolled_back_source_sha": baseline["baseline_source_sha"],
                "rolled_back_runtime_bundle_id": baseline[
                    "baseline_runtime_bundle_id"
                ],
                "rolled_back_schema_revision": baseline[
                    "baseline_schema_revision"
                ],
                "rolled_back_release_role": baseline[
                    "baseline_release_role"
                ],
                "disposition": args.disposition,
                "resolution_sha256": resolution_sha,
                "baseline_resolution_sha256": baseline_sha,
                "backend_off_report_sha256": off_report_sha,
                "api_report_sha256": api_sha,
                "approval_sha256": approval_sha,
                "recorded_at": _aware(api["observed_at"]).isoformat(),
            }
            raw = _canonical(report) + b"\n"
            recovery_sha = hashlib.sha256(raw).hexdigest()
            store.put_create_once(object_key, raw)
            cursor.execute(
                "INSERT INTO release_observation_recoveries "
                "(id,observation_run_id,resolution_sha256,worker_report_sha256,"
                "api_report_sha256,approval_sha256,disposition,"
                "recovery_report_sha256,private_object_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid4()),
                    run["id"],
                    resolution_sha,
                    off_report_sha,
                    api_sha,
                    approval_sha,
                    args.disposition,
                    recovery_sha,
                    object_key,
                ),
            )
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--resolution-report", required=True)
    result.add_argument("--baseline-resolution", required=True)
    result.add_argument("--off-report", required=True)
    result.add_argument("--api-report", required=True)
    result.add_argument("--disposition", required=True)
    result.add_argument(
        "--approval-id-env",
        default="PRODUCTION_RECOVERY_APPROVAL_ID",
    )
    result.add_argument(
        "--database-url-env",
        default="PRODUCTION_MIGRATION_DATABASE_URL",
    )
    result.add_argument(
        "--api-signing-key-env",
        default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
    )
    result.add_argument(
        "--private-evidence-store-id-env",
        default="PRIVATE_EVIDENCE_STORE_ID",
    )
    result.add_argument(
        "--private-evidence-token-env",
        default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN",
    )
    result.add_argument("--output", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = complete(args)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
