#!/usr/bin/env python3
"""Durable short-job observation state machine for COMMERCIAL_7A."""

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
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.apply_activation_plan import apply_phase, _load_object  # noqa: E402
from scripts.release.private_evidence_store import PrivateBlobEvidenceStore  # noqa: E402
from scripts.release.register_bundle import (  # noqa: E402
    COMMERCIAL_7A_PHASES,
    build_chained_phase_report,
    build_phase_evidence,
    phase_object_key,
)


METRIC_FIELDS = {
    "unresolved_p0_p1",
    "unhandled_signed_webhooks",
    "ledger_reconciliation_failures",
    "backend_runtime_age_seconds",
    "oldest_mandatory_outbox_age_seconds",
    "synthetic_flow_dlq",
    "acceptance_prefix_deletion_failures",
    "cleanup_status",
    "cleanup_cycle_sha256",
    "rls_policy_gap_count",
    "legacy_identity_fallback_count",
    "flag_bundle_drift",
}
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SAMPLE_FIELDS = {
    "schema",
    "passed",
    "observation_run_id",
    "source_sha",
    "manifest_sha256",
    "runtime_bundle_id",
    "api_deployment_id",
    "target_snapshot_hash",
    "bucket_started_at",
    "observed_at",
    "metrics",
    "signature",
}
FINAL_FIELDS = {
    "schema",
    "passed",
    "observation_run_id",
    "activation_id",
    "source_sha",
    "manifest_sha256",
    "runtime_bundle_id",
    "api_deployment_id",
    "target_snapshot_hash",
    "window_started_at",
    "window_deadline_at",
    "sample_count",
    "sample_sha256",
    "sample_observed_at",
    "maximum_gap_seconds",
    "cleanup_cycle_sha256",
    "produced_at",
}
FINAL_INDEX_FIELDS = {
    "schema",
    "passed",
    "observation_run_id",
    "source_sha",
    "final_report_sha256",
    "sample_sha256",
}
API_RUNTIME_REPORT_FIELDS = {
    "schema",
    "passed",
    "source_sha",
    "runtime_bundle_id",
    "api_deployment_id",
    "schema_revision",
    "release_role",
    "liveness_response_sha256",
    "readiness_response_sha256",
    "version_response_sha256",
    "observed_at",
    "signature",
}


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("observation database URL is invalid")
    return clean


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _write_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _bounded_report(path: str | Path, *, label: str) -> tuple[dict[str, Any], str]:
    report_path = Path(path)
    if (
        not report_path.is_file()
        or report_path.is_symlink()
        or report_path.stat().st_size <= 0
        or report_path.stat().st_size > 1_000_000
    ):
        raise ValueError(f"{label} must be one bounded regular file")
    raw = report_path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _verify_hmac_report(
    payload: dict[str, Any],
    *,
    expected_schema: str,
    signing_key: bytes,
    label: str,
) -> None:
    if (
        payload.get("schema") != expected_schema
        or payload.get("passed") is not True
        or len(signing_key) < 32
    ):
        raise ValueError(f"{label} identity is invalid")
    signature = str(payload.get("signature") or "")
    unsigned = dict(payload)
    unsigned.pop("signature", None)
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if (
        not signature.startswith("hmac-sha256:")
        or not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted)
    ):
        raise ValueError(f"{label} signature is invalid")


def validate_metric_values(
    metrics: dict[str, Any], *, require_cleanup: bool = False
) -> dict[str, Any]:
    if not isinstance(metrics, dict) or set(metrics) != METRIC_FIELDS:
        raise ValueError("observation metrics fields are invalid")
    metrics = dict(metrics)
    cleanup_status = metrics["cleanup_status"]
    cleanup_sha = metrics["cleanup_cycle_sha256"]
    cleanup_valid = (
        cleanup_status == "PASS" and SHA64.fullmatch(str(cleanup_sha or ""))
    ) or (cleanup_status == "PENDING" and cleanup_sha is None)
    if not cleanup_valid or (require_cleanup and cleanup_status != "PASS"):
        raise ValueError("observation cleanup-cycle metrics are invalid")
    zero_fields = (
        "unresolved_p0_p1",
        "unhandled_signed_webhooks",
        "ledger_reconciliation_failures",
        "synthetic_flow_dlq",
        "acceptance_prefix_deletion_failures",
        "rls_policy_gap_count",
        "legacy_identity_fallback_count",
        "flag_bundle_drift",
    )
    if any(type(metrics[name]) is not int or metrics[name] != 0 for name in zero_fields):
        raise ValueError("observation detected a nonzero blocking counter")
    if (
        type(metrics["backend_runtime_age_seconds"]) not in {int, float}
        or not 0 <= metrics["backend_runtime_age_seconds"] <= 120
        or type(metrics["oldest_mandatory_outbox_age_seconds"]) not in {int, float}
        or not 0 <= metrics["oldest_mandatory_outbox_age_seconds"] <= 300
    ):
        raise ValueError("observation threshold failed")
    return metrics


def validate_metrics(payload: dict[str, Any], *, run: dict[str, Any]) -> dict[str, Any]:
    if (
        set(payload) != {"schema", "observation_run_id", "source_sha", "metrics", "observed_at"}
        or payload.get("schema") != "vowpic.observation-metrics-input.v1"
        or str(payload.get("observation_run_id")) != str(run["id"])
        or payload.get("source_sha") != run["source_sha"]
        or not isinstance(payload.get("metrics"), dict)
    ):
        raise ValueError("observation metrics input is invalid")
    metrics = validate_metric_values(payload["metrics"])
    observed = _aware(payload["observed_at"])
    now = datetime.now(timezone.utc)
    if observed > now + timedelta(minutes=2) or now - observed > timedelta(minutes=10):
        raise ValueError("observation metrics input is stale")
    return metrics


def _validate_sample_report(
    report: dict[str, Any],
    *,
    row: dict[str, Any],
    run: dict[str, Any],
    signing_key: bytes,
    maximum_gap_minutes: int,
) -> tuple[datetime, dict[str, Any]]:
    if not isinstance(report, dict) or set(report) != SAMPLE_FIELDS:
        raise ValueError("observation sample fields are invalid")
    expected = {
        "schema": "vowpic.observation-sample.v1",
        "passed": True,
        "observation_run_id": str(run["id"]),
        "source_sha": run["source_sha"],
        "manifest_sha256": run["manifest_sha256"],
        "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
        "target_snapshot_hash": run["target_snapshot_hash"],
    }
    if any(report.get(field) != value for field, value in expected.items()):
        raise ValueError("observation sample coordinates drifted")
    bucket = _aware(report["bucket_started_at"])
    observed = _aware(report["observed_at"])
    if bucket != _aware(row["bucket_started_at"]):
        raise ValueError("observation sample bucket disagrees with the database")
    if (
        observed < _aware(run["started_at"])
        or observed
        > _aware(run["deadline_at"]) + timedelta(minutes=maximum_gap_minutes)
    ):
        raise ValueError("observation sample is outside the accepted window")
    metrics = validate_metric_values(report["metrics"])
    if metrics != dict(row["metrics_json"]):
        raise ValueError("observation sample metrics disagree with the database")
    signature = str(report["signature"])
    if signature != str(row["signature"]):
        raise ValueError("observation sample signature disagrees with the database")
    unsigned = dict(report)
    del unsigned["signature"]
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if (
        not signature.startswith("hmac-sha256:")
        or not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted)
    ):
        raise ValueError("observation sample signature mismatch")
    return observed, metrics


def _validate_final_documents(
    *,
    lease: dict[str, Any],
    final: dict[str, Any],
    index: dict[str, Any],
    run: dict[str, Any],
    maximum_gap_minutes: int = 15,
) -> tuple[bytes, bytes]:
    if (
        not isinstance(lease, dict)
        or set(lease)
        != {
            "schema",
            "passed",
            "observation_run_id",
            "state",
            "minimum_hours",
            "maximum_gap_minutes",
        }
        or lease.get("schema") != "vowpic.observation-finalization-lease.v1"
        or lease.get("passed") is not True
        or lease.get("observation_run_id") != str(run["id"])
        or lease.get("state") != "FINALIZING"
        or type(lease.get("minimum_hours")) is not int
        or lease["minimum_hours"] < 24
        or type(lease.get("maximum_gap_minutes")) is not int
        or lease["maximum_gap_minutes"] != maximum_gap_minutes
    ):
        raise ValueError("observation finalization lease is invalid")
    if not isinstance(final, dict) or set(final) != FINAL_FIELDS:
        raise ValueError("observation final report fields are invalid")
    expected_final = {
        "schema": "vowpic.observation-final-report.v1",
        "passed": True,
        "observation_run_id": str(run["id"]),
        "activation_id": str(run["release_activation_id"]),
        "source_sha": run["source_sha"],
        "manifest_sha256": run["manifest_sha256"],
        "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
        "target_snapshot_hash": run["target_snapshot_hash"],
        "window_started_at": _aware(run["started_at"]).isoformat(),
        "window_deadline_at": _aware(run["deadline_at"]).isoformat(),
        "produced_at": _aware(run["deadline_at"]).isoformat(),
        "cleanup_cycle_sha256": run["cleanup_cycle_sha256"],
    }
    if any(final.get(field) != value for field, value in expected_final.items()):
        raise ValueError("observation final report coordinates drifted")
    hashes = final.get("sample_sha256")
    observed_values = final.get("sample_observed_at")
    parsed_observed = (
        [_aware(value) for value in observed_values]
        if isinstance(observed_values, list)
        else []
    )
    started = _aware(run["started_at"])
    deadline = _aware(run["deadline_at"])
    coverage_points = sorted(
        {started, deadline, *(min(value, deadline) for value in parsed_observed)}
    )
    calculated_gap = (
        max(
            (later - earlier).total_seconds()
            for earlier, later in zip(coverage_points, coverage_points[1:])
        )
        if len(coverage_points) > 1
        else float("inf")
    )
    if (
        type(final.get("sample_count")) is not int
        or final["sample_count"] <= 0
        or not isinstance(hashes, list)
        or not isinstance(observed_values, list)
        or final["sample_count"] != len(hashes)
        or len(hashes) != len(observed_values)
        or any(not SHA64.fullmatch(str(value)) for value in hashes)
        or len(set(hashes)) != len(hashes)
        or parsed_observed != sorted(parsed_observed)
        or len(set(parsed_observed)) != len(parsed_observed)
        or any(
            value < started
            or value > deadline + timedelta(minutes=maximum_gap_minutes)
            for value in parsed_observed
        )
        or type(final.get("maximum_gap_seconds")) is not int
        or not 0 <= final["maximum_gap_seconds"] <= maximum_gap_minutes * 60
        or final["maximum_gap_seconds"] != int(calculated_gap)
        or not SHA64.fullmatch(str(final.get("cleanup_cycle_sha256") or ""))
    ):
        raise ValueError("observation final report coverage is invalid")
    if not isinstance(index, dict) or set(index) != FINAL_INDEX_FIELDS:
        raise ValueError("observation final index fields are invalid")
    final_raw = _canonical(final) + b"\n"
    expected_index = {
        "schema": "vowpic.observation-final-index.v1",
        "passed": True,
        "observation_run_id": str(run["id"]),
        "source_sha": run["source_sha"],
        "final_report_sha256": hashlib.sha256(final_raw).hexdigest(),
        "sample_sha256": hashes,
    }
    if any(index.get(field) != value for field, value in expected_index.items()):
        raise ValueError("observation final index is invalid")
    return final_raw, _canonical(index) + b"\n"


def _load_run(cursor: Any, run_id: str, *, lock: bool = False) -> dict[str, Any]:
    UUID(run_id)
    suffix = " FOR UPDATE" if lock else ""
    cursor.execute(
        """
        SELECT o.*, a.source_sha, a.kind AS release_kind, a.phase AS release_phase,
               a.private_evidence_prefix, a.approval, a.phase_rank AS release_phase_rank,
               a.report_sha256 AS release_report_sha256
        FROM release_observation_runs o
        JOIN release_activations a ON a.id = o.release_activation_id
        WHERE o.id = %s
        """ + suffix,
        (run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("observation run is missing")
    return dict(row)


def _start(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    manifest = _load_object(args.manifest)
    backend_runtime = _load_object(args.backend_runtime_report)
    target = _load_object(args.target_snapshot_report)
    if args.minimum_hours < 24 or manifest.get("source_sha") != args.source_sha:
        raise ValueError("observation start contract is invalid")
    database_url = os.environ.get(args.database_url_env, "")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("vowpic-observation",))
            cursor.execute(
                "SELECT * FROM release_activations WHERE environment='production' "
                "AND kind='COMMERCIAL_7A' AND source_sha=%s AND phase='ACTIVATED' "
                "ORDER BY updated_at DESC LIMIT 2 FOR UPDATE",
                (args.source_sha,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if len(rows) != 1:
                raise ValueError("exactly one activated release is required")
            activation = rows[0]
            expected = {
                "runtime_bundle_id": activation["runtime_bundle_id"],
                "deployment_id": activation["api_deployment_id"],
                "manifest_sha256": activation["manifest_sha256"],
            }
            _verify_hmac_report(
                backend_runtime,
                expected_schema="vowpic.api-runtime-coordinate-report.v1",
                signing_key=os.environ.get(args.runtime_signing_key_env, "").encode(),
                label="observation backend runtime report",
            )
            if (
                hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest() != activation["manifest_sha256"]
                or set(backend_runtime) != API_RUNTIME_REPORT_FIELDS
                or backend_runtime.get("passed") is not True
                or backend_runtime.get("source_sha") != activation["source_sha"]
                or backend_runtime.get("runtime_bundle_id") != activation["runtime_bundle_id"]
                or backend_runtime.get("api_deployment_id") != activation["api_deployment_id"]
                or any(
                    activation.get(field) is not None
                    for field in ("worker_deployment_id", "worker_role", "worker_image_digest")
                )
                or target.get("passed") is not True
                or target.get("target_snapshot_hash") != activation["target_snapshot_hash"]
            ):
                raise ValueError("observation start evidence binding mismatch")
            cursor.execute(
                "SELECT * FROM release_observation_runs WHERE release_activation_id=%s FOR UPDATE",
                (activation["id"],),
            )
            row = cursor.fetchone()
            if row is None:
                started = datetime.now(timezone.utc)
                cursor.execute(
                    """
                    INSERT INTO release_observation_runs (
                      id, release_activation_id, manifest_sha256, runtime_bundle_id,
                      api_deployment_id, worker_deployment_id, worker_image_digest,
                      current_snapshot_hash, target_snapshot_hash, state,
                      started_at, deadline_at, version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'OBSERVING',%s,%s,1)
                    RETURNING *
                    """,
                    (
                        str(uuid4()), activation["id"], activation["manifest_sha256"],
                        activation["runtime_bundle_id"], activation["api_deployment_id"],
                        None, None,
                        activation["current_snapshot_hash"], activation["target_snapshot_hash"],
                        started, started + timedelta(hours=args.minimum_hours),
                    ),
                )
                row = cursor.fetchone()
            run = dict(row)
    return {
        "schema": "vowpic.observation-start.v1", "passed": True,
        "observation_run_id": str(run["id"]), "source_sha": args.source_sha,
        **expected, "started_at": run["started_at"].isoformat(),
        "deadline_at": run["deadline_at"].isoformat(),
    }


def _bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(os.environ.get(args.database_url_env, ""))) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            filters = "o.id=%s" if args.requested_observation_run_id else "o.state IN ('OBSERVING','FINALIZING','FAILED')"
            values = (args.requested_observation_run_id,) if args.requested_observation_run_id else ()
            cursor.execute(
                "SELECT o.*,a.source_sha,a.kind AS release_role FROM release_observation_runs o "
                "JOIN release_activations a ON a.id=o.release_activation_id WHERE " + filters +
                " ORDER BY o.updated_at DESC LIMIT 2",
                values,
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if not rows and args.allow_none:
        return {"active": False}
    if len(rows) != 1:
        raise ValueError("observation bootstrap is absent or ambiguous")
    row = rows[0]
    return {
        "active": row["state"] in {"OBSERVING", "FINALIZING"},
        "source_sha": row["source_sha"],
        "observation_run_id": str(row["id"]),
        "release_role": row["release_role"],
        "state": row["state"],
        "runtime_bundle_id": row["runtime_bundle_id"],
        "api_deployment_id": row["api_deployment_id"],
    }


def _sample(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    metrics_path = os.environ.get(args.metrics_input_path_env, "").strip()
    if not metrics_path:
        raise ValueError("NOT_RUN: dedicated observation metrics input is required")
    metrics_input = _load_object(metrics_path)
    database_url = os.environ.get(args.database_url_env, "")
    bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket -= timedelta(minutes=bucket.minute % 5)
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (
                    "vowpic-observation-sample:"
                    f"{args.observation_run_id}:{bucket.isoformat()}",
                ),
            )
            run = _load_run(cursor, args.observation_run_id)
            if run["state"] != "OBSERVING" or run["source_sha"] != args.expected_source_sha:
                raise ValueError("observation sample run is not active")
            metrics = validate_metrics(metrics_input, run=run)
            observed_at = _aware(metrics_input["observed_at"])
            if (
                observed_at < run["started_at"]
                or observed_at > run["deadline_at"] + timedelta(minutes=15)
            ):
                raise ValueError("observation metrics timestamp is outside the run window")
            unsigned = {
                "schema": "vowpic.observation-sample.v1", "passed": True,
                "observation_run_id": str(run["id"]), "source_sha": run["source_sha"],
                "manifest_sha256": run["manifest_sha256"],
                "runtime_bundle_id": run["runtime_bundle_id"],
                "api_deployment_id": run["api_deployment_id"],
                "target_snapshot_hash": run["target_snapshot_hash"],
                "bucket_started_at": bucket.isoformat(),
                "observed_at": observed_at.isoformat(),
                "metrics": metrics,
            }
            key = os.environ.get(args.signing_key_env, "").encode()
            if len(key) < 32:
                raise ValueError("observation signing key is required")
            signature = hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest()
            report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
            raw = _canonical(report) + b"\n"
            digest = hashlib.sha256(raw).hexdigest()
            prefix = str(run["private_evidence_prefix"]).strip("/\\")
            object_key = f"{prefix}/observations/{run['id']}/{bucket.strftime('%Y%m%dT%H%MZ')}.json"
            store = PrivateBlobEvidenceStore(
                store_id=os.environ.get(args.private_evidence_store_id_env, ""),
                token=os.environ.get(args.private_evidence_token_env, ""),
            )
            cursor.execute(
                "SELECT * FROM release_observation_samples WHERE observation_run_id=%s "
                "AND bucket_started_at=%s", (run["id"], bucket),
            )
            existing = cursor.fetchone()
            if existing is None:
                store.put_create_once(object_key, raw)
                cursor.execute(
                    "INSERT INTO release_observation_samples (id,observation_run_id,bucket_started_at,"
                    "sample_sha256,signature,metrics_json) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (str(uuid4()), run["id"], bucket, digest, report["signature"], json.dumps(metrics)),
                )
            else:
                existing_raw = store.read(object_key)
                if hashlib.sha256(existing_raw).hexdigest() != existing["sample_sha256"]:
                    raise ValueError("observation sample bucket Private Blob drifted")
                existing_report = json.loads(existing_raw)
                _, existing_metrics = _validate_sample_report(
                    existing_report,
                    row=dict(existing),
                    run=run,
                    signing_key=key,
                    maximum_gap_minutes=15,
                )
                report = existing_report
                metrics = existing_metrics
    return report


def _prepare_finalize(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    signing_key = os.environ.get(args.signing_key_env, "").encode()
    if args.require_cleanup_cycle and len(signing_key) < 32:
        raise ValueError("observation signing key is required for finalization")
    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    with psycopg2.connect(_database_url(os.environ.get(args.database_url_env, ""))) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            run = _load_run(cursor, args.observation_run_id, lock=True)
            duration = _aware(run["deadline_at"]) - _aware(run["started_at"])
            if (
                args.minimum_hours < 24
                or args.maximum_gap_minutes <= 0
                or args.maximum_gap_minutes > 15
                or duration < timedelta(hours=args.minimum_hours)
            ):
                raise ValueError("observation finalization window contract is invalid")
            cleanup_hashes: set[str] = set()
            if args.require_cleanup_cycle:
                cursor.execute(
                    "SELECT * FROM release_observation_samples "
                    "WHERE observation_run_id=%s ORDER BY bucket_started_at",
                    (run["id"],),
                )
                prefix = str(run["private_evidence_prefix"]).strip("/\\")
                for row in cursor.fetchall():
                    sample = dict(row)
                    bucket = sample["bucket_started_at"]
                    object_key = (
                        f"{prefix}/observations/{run['id']}/"
                        f"{bucket.strftime('%Y%m%dT%H%MZ')}.json"
                    )
                    raw = store.read(object_key)
                    if hashlib.sha256(raw).hexdigest() != sample["sample_sha256"]:
                        raise ValueError(
                            "observation cleanup sample Private Blob hash drift"
                        )
                    report = json.loads(raw)
                    _, metrics = _validate_sample_report(
                        report,
                        row=sample,
                        run=run,
                        signing_key=signing_key,
                        maximum_gap_minutes=args.maximum_gap_minutes,
                    )
                    if metrics["cleanup_status"] == "PASS":
                        cleanup_hashes.add(metrics["cleanup_cycle_sha256"])
                if len(cleanup_hashes) != 1:
                    raise ValueError(
                        "observation cleanup cycle is not uniquely proven"
                    )
            cleanup_hash = (
                next(iter(cleanup_hashes))
                if cleanup_hashes
                else run.get("cleanup_cycle_sha256")
            )
            if run["state"] == "FINALIZING":
                if (
                    args.require_cleanup_cycle
                    and run.get("cleanup_cycle_sha256") != cleanup_hash
                ):
                    raise ValueError(
                        "finalizing observation cleanup hash drifted"
                    )
            elif (
                run["state"] != "OBSERVING"
                or datetime.now(timezone.utc) < run["deadline_at"]
            ):
                raise ValueError("observation is not eligible for finalization")
            else:
                cursor.execute(
                    "UPDATE release_observation_runs SET state='FINALIZING',"
                    "cleanup_cycle_sha256=%s,finalizer=%s,"
                    "version=version+1 WHERE id=%s AND version=%s AND state='OBSERVING'",
                    (
                        cleanup_hash,
                        f"github-actions:{os.environ.get('GITHUB_RUN_ID','unknown')}",
                        run["id"],
                        run["version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("observation finalization CAS failed")
    return {
        "schema": "vowpic.observation-finalization-lease.v1", "passed": True,
        "observation_run_id": args.observation_run_id, "state": "FINALIZING",
        "minimum_hours": args.minimum_hours, "maximum_gap_minutes": args.maximum_gap_minutes,
    }


def _aggregate(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    key = os.environ.get(args.signing_key_env, "").encode()
    if len(key) < 32:
        raise ValueError("observation signing key is required for aggregation")
    with psycopg2.connect(_database_url(os.environ.get(args.database_url_env, ""))) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            run = _load_run(cursor, args.observation_run_id)
            cursor.execute(
                "SELECT * FROM release_observation_samples WHERE observation_run_id=%s "
                "ORDER BY bucket_started_at", (run["id"],),
            )
            samples = [dict(row) for row in cursor.fetchall()]
    if (
        run["state"] != "FINALIZING"
        or not samples
        or not SHA64.fullmatch(str(run.get("cleanup_cycle_sha256") or ""))
        or args.maximum_gap_minutes <= 0
        or args.maximum_gap_minutes > 15
    ):
        raise ValueError("observation finalization samples are unavailable")
    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    prefix = str(run["private_evidence_prefix"]).strip("/\\")
    hashes: list[str] = []
    observed_values: list[datetime] = []
    cleanup_proven = False
    for row in samples:
        bucket = row["bucket_started_at"]
        object_key = f"{prefix}/observations/{run['id']}/{bucket.strftime('%Y%m%dT%H%MZ')}.json"
        raw = store.read(object_key)
        if hashlib.sha256(raw).hexdigest() != row["sample_sha256"]:
            raise ValueError("observation sample Private Blob hash drift")
        report = json.loads(raw)
        observed, metrics = _validate_sample_report(
            report,
            row=row,
            run=run,
            signing_key=key,
            maximum_gap_minutes=args.maximum_gap_minutes,
        )
        if metrics["cleanup_status"] == "PASS":
            if metrics["cleanup_cycle_sha256"] != run["cleanup_cycle_sha256"]:
                raise ValueError("observation cleanup-cycle hash drifted")
            cleanup_proven = True
        hashes.append(row["sample_sha256"])
        observed_values.append(observed)
    if not cleanup_proven:
        raise ValueError("observation cleanup cycle is not proven by a signed sample")
    started = _aware(run["started_at"])
    deadline = _aware(run["deadline_at"])
    effective_points = {
        started,
        deadline,
        *(min(value, deadline) for value in observed_values),
    }
    points = sorted(effective_points)
    maximum_gap = max(
        (later - earlier).total_seconds()
        for earlier, later in zip(points, points[1:])
    )
    if maximum_gap > args.maximum_gap_minutes * 60:
        raise ValueError("observation sample gap exceeds the contract")
    final = {
        "schema": "vowpic.observation-final-report.v1", "passed": True,
        "observation_run_id": str(run["id"]), "activation_id": str(run["release_activation_id"]),
        "source_sha": run["source_sha"], "manifest_sha256": run["manifest_sha256"],
        "runtime_bundle_id": run["runtime_bundle_id"], "api_deployment_id": run["api_deployment_id"],
        "target_snapshot_hash": run["target_snapshot_hash"],
        "window_started_at": started.isoformat(),
        "window_deadline_at": deadline.isoformat(),
        "sample_count": len(samples), "sample_sha256": hashes,
        "sample_observed_at": [value.isoformat() for value in observed_values],
        "maximum_gap_seconds": int(maximum_gap), "cleanup_cycle_sha256": run["cleanup_cycle_sha256"],
        "produced_at": deadline.isoformat(),
    }
    index = {
        "schema": "vowpic.observation-final-index.v1", "passed": True,
        "observation_run_id": str(run["id"]),
        "source_sha": run["source_sha"],
        "final_report_sha256": hashlib.sha256(_canonical(final) + b"\n").hexdigest(),
        "sample_sha256": hashes,
    }
    final_raw, index_raw = _validate_final_documents(
        lease={
            "schema": "vowpic.observation-finalization-lease.v1",
            "passed": True,
            "observation_run_id": str(run["id"]),
            "state": "FINALIZING",
            "minimum_hours": 24,
            "maximum_gap_minutes": args.maximum_gap_minutes,
        },
        final=final,
        index=index,
        run=run,
        maximum_gap_minutes=args.maximum_gap_minutes,
    )
    store.put_create_once(
        f"{prefix}/observations/{run['id']}/final-report.json", final_raw
    )
    store.put_create_once(
        f"{prefix}/observations/{run['id']}/final-index.json", index_raw
    )
    return final, index


def _complete_finalize(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor

    lease = _load_object(args.finalization_lease_report)
    final = _load_object(args.final_report)
    index = _load_object(args.final_index_report)
    approval = os.environ.get(args.approval_id_env, "").strip()
    if not approval:
        raise ValueError("observation finalization evidence is invalid")
    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    database_url = os.environ.get(args.database_url_env, "")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("vowpic-observation",))
            run = _load_run(cursor, args.observation_run_id, lock=True)
            cursor.execute("SELECT * FROM release_activations WHERE id=%s FOR UPDATE", (run["release_activation_id"],))
            activation = dict(cursor.fetchone())
            if (
                run["state"] != args.expected_state or activation["phase"] != args.expected_release_phase
                or activation["approval"] != approval
            ):
                raise ValueError("observation finalization state drifted")
            prefix = str(activation["private_evidence_prefix"]).strip("/\\")
            final_raw, index_raw = _validate_final_documents(
                lease=lease,
                final=final,
                index=index,
                run=run,
            )
            cursor.execute(
                "SELECT sample_sha256 FROM release_observation_samples "
                "WHERE observation_run_id=%s ORDER BY bucket_started_at",
                (run["id"],),
            )
            database_hashes = [row["sample_sha256"] for row in cursor.fetchall()]
            if database_hashes != final["sample_sha256"]:
                raise ValueError("observation final sample index disagrees with the database")
            final_key = f"{prefix}/observations/{run['id']}/final-report.json"
            index_key = f"{prefix}/observations/{run['id']}/final-index.json"
            if store.read(final_key) != final_raw or store.read(index_key) != index_raw:
                raise ValueError("observation final Private Blob evidence drifted")
            previous_raw = store.read(phase_object_key(prefix, "OBSERVING"))
            if hashlib.sha256(previous_raw).hexdigest() != activation["report_sha256"]:
                raise ValueError("observation predecessor phase evidence drifted")
            phase_evidence = build_phase_evidence(
                phase=args.final_release_phase,
                evidence={"observation-final-report": Path(args.final_report), "final-index-report": Path(args.final_index_report)},
                coordinates={"observation_run_id": str(run["id"]), "manifest_sha256": run["manifest_sha256"]},
            )
            phase_report, raw, digest = build_chained_phase_report(
                activation=activation, phase_evidence=phase_evidence,
                private_evidence_prefix=prefix, previous_report=json.loads(previous_raw),
            )
            object_key = phase_object_key(prefix, args.final_release_phase)
            store.put_create_once(object_key, raw)
            rank = COMMERCIAL_7A_PHASES.index(args.final_release_phase)
            cursor.execute(
                "UPDATE release_activations SET phase=%s,phase_rank=%s,report_sha256=%s,version=version+1 "
                "WHERE id=%s AND version=%s AND phase=%s",
                (args.final_release_phase, rank, digest, activation["id"], activation["version"], args.expected_release_phase),
            )
            if cursor.rowcount != 1:
                raise ValueError("release final acceptance CAS failed")
            cursor.execute(
                "INSERT INTO release_phase_evidence (id,release_activation_id,phase,phase_rank,report_sha256,"
                "private_object_key,coordinates_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid4()), activation["id"], args.final_release_phase, rank, digest, object_key,
                 Json(phase_report["phase_evidence"]["coordinates"])),
            )
            cursor.execute(
                "UPDATE release_observation_runs SET state='PASSED',finalized_at=CURRENT_TIMESTAMP,"
                "version=version+1 WHERE id=%s AND version=%s AND state=%s",
                (run["id"], run["version"], args.expected_state),
            )
            if cursor.rowcount != 1:
                raise ValueError("observation PASS CAS failed")
    return {
        "schema": "vowpic.observation-complete.v1", "passed": True,
        "observation_run_id": args.observation_run_id, "state": "PASSED",
        "release_phase": args.final_release_phase, "release_report_sha256": digest,
    }


def _fail(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    database_url = os.environ.get(args.database_url_env, "")
    action = "FAIL_AND_SHUTDOWN"
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            run = _load_run(cursor, args.observation_run_id, lock=True)
            if run["state"] == "PASSED":
                cursor.execute(
                    "SELECT phase,report_sha256 FROM release_activations WHERE id=%s",
                    (run["release_activation_id"],),
                )
                activation = cursor.fetchone()
                if (
                    activation is None
                    or activation["phase"] != "7A_ACCEPTED"
                    or not SHA64.fullmatch(str(activation["report_sha256"] or ""))
                ):
                    raise ValueError("passed observation is not atomically accepted")
                action = "ALREADY_PASSED"
            elif run["state"] == "FINALIZING":
                try:
                    store = PrivateBlobEvidenceStore(
                        store_id=os.environ.get(
                            args.private_evidence_store_id_env, ""
                        ),
                        token=os.environ.get(
                            args.private_evidence_token_env, ""
                        ),
                    )
                    prefix = str(run["private_evidence_prefix"]).strip("/\\")
                    final = json.loads(
                        store.read(
                            f"{prefix}/observations/{run['id']}/final-report.json"
                        )
                    )
                    index = json.loads(
                        store.read(
                            f"{prefix}/observations/{run['id']}/final-index.json"
                        )
                    )
                    _validate_final_documents(
                        lease={
                            "schema": "vowpic.observation-finalization-lease.v1",
                            "passed": True,
                            "observation_run_id": str(run["id"]),
                            "state": "FINALIZING",
                            "minimum_hours": 24,
                            "maximum_gap_minutes": 15,
                        },
                        final=final,
                        index=index,
                        run=run,
                    )
                    cursor.execute(
                        "SELECT sample_sha256 FROM release_observation_samples "
                        "WHERE observation_run_id=%s ORDER BY bucket_started_at",
                        (run["id"],),
                    )
                    if [row["sample_sha256"] for row in cursor.fetchall()] != final[
                        "sample_sha256"
                    ]:
                        raise ValueError(
                            "finalizing observation sample index drifted"
                        )
                except Exception:
                    action = "FAIL_AND_SHUTDOWN"
                else:
                    action = "RESUME_FINALIZATION"
            if action == "FAIL_AND_SHUTDOWN" and run["state"] != "FAILED":
                cursor.execute(
                    "UPDATE release_observation_runs SET state='FAILED',version=version+1 "
                    "WHERE id=%s AND version=%s", (run["id"], run["version"]),
                )
            approval = run["approval"]
            activation_id = str(run["release_activation_id"])
    if args.off_only and action == "FAIL_AND_SHUTDOWN":
        plan = _load_object(ROOT / "release/activation-plan.json")
        apply_phase(
            database_url, phase="emergency-off", plan=plan, approval=approval,
            deployment_id=run["api_deployment_id"], source_sha=run["source_sha"],
            binding_report=None, cohort_user_id=None,
        )
    return {
        "schema": "vowpic.observation-failure.v1",
        "passed": True,
        "state": "FAILED" if action == "FAIL_AND_SHUTDOWN" else run["state"],
        "action": action,
        "requires_shutdown": action == "FAIL_AND_SHUTDOWN",
        "observation_run_id": args.observation_run_id, "activation_id": activation_id,
        "source_sha": run["source_sha"], "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
    }


def _resolve_recovery(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(os.environ.get(args.database_url_env, ""))) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            run = _load_run(cursor, args.observation_run_id)
            if run["state"] != "FAILED" or run["release_kind"] != "COMMERCIAL_7A":
                raise ValueError("observation recovery is not a failed COMMERCIAL_7A run")
            cursor.execute(
                "SELECT coordinates_json FROM release_phase_evidence WHERE release_activation_id=%s "
                "AND phase='MANIFEST_SEALED'", (run["release_activation_id"],),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("observation recovery baseline coordinates are missing")
            coords = row["coordinates_json"]
    return {
        "schema": "vowpic.observation-recovery-resolution.v1", "passed": True,
        "observation_run_id": args.observation_run_id, "release_role": "COMMERCIAL_7A",
        "source_sha": run["source_sha"], "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
        "private_compatible_baseline_url": coords["private_compatible_baseline_deployment_url"],
        "private_compatible_baseline_deployment_id": coords["private_compatible_baseline_deployment_id"],
        "schema_revision": "20260710_0020",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--manifest", required=True); start.add_argument("--backend-runtime-report", required=True)
    start.add_argument("--target-snapshot-report", required=True); start.add_argument("--source-sha", required=True)
    start.add_argument("--minimum-hours", type=int, default=24); start.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    start.add_argument("--runtime-signing-key-env", default="RELEASE_EVIDENCE_HMAC_KEY")
    boot = sub.add_parser("bootstrap"); boot.add_argument("--requested-observation-run-id")
    boot.add_argument("--allow-none", action="store_true"); boot.add_argument("--job-output")
    boot.add_argument("--database-url-env", default="OBSERVATION_DATABASE_URL")
    sample = sub.add_parser("sample"); sample.add_argument("--observation-run-id", required=True)
    sample.add_argument("--expected-source-sha", required=True); sample.add_argument("--database-url-env", default="OBSERVATION_DATABASE_URL")
    sample.add_argument("--metrics-input-path-env", default="OBSERVATION_METRICS_INPUT_PATH")
    sample.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    sample.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN")
    sample.add_argument("--signing-key-env", default="OBSERVATION_SIGNING_KEY")
    prep = sub.add_parser("prepare-finalize"); prep.add_argument("--observation-run-id", required=True)
    prep.add_argument("--release-kind"); prep.add_argument("--minimum-hours", type=int, default=24)
    prep.add_argument("--maximum-gap-minutes", type=int, default=15); prep.add_argument("--require-cleanup-cycle", action="store_true")
    prep.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    prep.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    prep.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN")
    prep.add_argument("--signing-key-env", default="OBSERVATION_SIGNING_KEY")
    aggregate = sub.add_parser("aggregate"); aggregate.add_argument("--observation-run-id", required=True)
    aggregate.add_argument("--maximum-gap-minutes", type=int, default=15); aggregate.add_argument("--index-output", required=True)
    aggregate.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    aggregate.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    aggregate.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN")
    aggregate.add_argument("--signing-key-env", default="OBSERVATION_SIGNING_KEY")
    complete = sub.add_parser("complete-finalize"); complete.add_argument("--observation-run-id", required=True)
    complete.add_argument("--release-kind"); complete.add_argument("--finalization-lease-report", required=True)
    complete.add_argument("--final-report", required=True); complete.add_argument("--final-index-report", required=True)
    complete.add_argument("--expected-state", default="FINALIZING"); complete.add_argument("--expected-release-phase", default="OBSERVING")
    complete.add_argument("--final-release-phase", default="7A_ACCEPTED"); complete.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    complete.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    complete.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    complete.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN")
    fail = sub.add_parser("fail"); fail.add_argument("--observation-run-id", required=True); fail.add_argument("--off-only", action="store_true")
    fail.add_argument("--database-url-env", default="OBSERVATION_EMERGENCY_DATABASE_URL")
    fail.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    fail.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_READ_TOKEN")
    recover = sub.add_parser("resolve-recovery"); recover.add_argument("--observation-run-id", required=True)
    recover.add_argument("--job-env"); recover.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    for command in sub.choices.values(): command.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "start": result = _start(args)
        elif args.command == "bootstrap": result = _bootstrap(args)
        elif args.command == "sample": result = _sample(args)
        elif args.command == "prepare-finalize": result = _prepare_finalize(args)
        elif args.command == "aggregate":
            result, index = _aggregate(args); _write_once(Path(args.index_output), index)
        elif args.command == "complete-finalize": result = _complete_finalize(args)
        elif args.command == "fail": result = _fail(args)
        elif args.command == "resolve-recovery": result = _resolve_recovery(args)
        else: raise ValueError("unknown observation command")
        _write_once(Path(args.output), result)
        if args.command == "bootstrap" and args.job_output:
            with Path(args.job_output).open("a", encoding="utf-8") as handle:
                for key in (
                    "active",
                    "source_sha",
                    "observation_run_id",
                    "release_role",
                    "state",
                    "runtime_bundle_id",
                    "api_deployment_id",
                ):
                    if key in result: handle.write(f"{key}={str(result[key]).lower() if isinstance(result[key], bool) else result[key]}\n")
        if args.command == "resolve-recovery" and args.job_env:
            with Path(args.job_env).open("a", encoding="utf-8") as handle:
                for key, value in result.items():
                    if isinstance(value, (str, int, bool)): handle.write(f"RECOVERY_{key.upper()}={value}\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
