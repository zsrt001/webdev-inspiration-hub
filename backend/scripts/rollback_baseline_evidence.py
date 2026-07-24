#!/usr/bin/env python3
"""Capture and resolve the exact signed rollback-baseline runtime coordinates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[2]
for location in (ROOT, ROOT / "backend"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from scripts.release.collect_runtime_report import (  # noqa: E402
    canonical_json_bytes,
    collect_api_runtime_coordinate_report,
)
from scripts.release.private_evidence_store import (  # noqa: E402
    PrivateBlobEvidenceStore,
)


SOURCE_SHA = re.compile(r"^[0-9a-f]{40,64}$")
RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
SCHEMA_REVISION = re.compile(r"^[0-9]{8}_[0-9]{4}$")
DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")
SIGNED_RUNTIME_FIELDS = {
    "schema",
    "passed",
    "source_sha",
    "runtime_bundle_id",
    "api_deployment_id",
    "schema_revision",
    "release_role",
    "runtime_environment",
    "backend_execution_version",
    "backend_executor_digest",
    "liveness_response_sha256",
    "readiness_response_sha256",
    "version_response_sha256",
    "observed_at",
    "signature",
}


def _database_url(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ValueError("database URL environment variable is absent")
    return value


def _exact_https_origin(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("rollback baseline URL must be one exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _load_active_release(
    database_url: str,
    *,
    target_source_sha: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    source_sha = str(target_source_sha or "").strip().lower()
    if not SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("target source SHA is invalid")
    with psycopg2.connect(database_url) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, source_sha, runtime_bundle_id, api_deployment_id,
                       private_evidence_prefix, phase, workflow_run_id,
                       workflow_attempt
                FROM release_activations
                WHERE environment='production' AND kind='COMMERCIAL_7A'
                  AND source_sha=%s
                  AND phase NOT IN ('7A_ACCEPTED','FAILED','CLEANED')
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (source_sha,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError("exactly one active COMMERCIAL_7A release is required")
    row = rows[0]
    prefix = str(row.get("private_evidence_prefix") or "").strip().strip("/\\")
    if not prefix or ".." in Path(prefix).parts:
        raise ValueError("release private evidence prefix is invalid")
    return row


def _verify_signed_runtime(
    payload: dict[str, Any],
    *,
    signing_key: bytes,
) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or set(payload) != SIGNED_RUNTIME_FIELDS
        or payload.get("schema") != "vowpic.api-runtime-coordinate-report.v1"
        or payload.get("passed") is not True
        or payload.get("release_role") != "SAFE_BASELINE"
        or payload.get("runtime_environment") != "production"
        or not SOURCE_SHA.fullmatch(str(payload.get("source_sha") or ""))
        or not RUNTIME_ID.fullmatch(str(payload.get("runtime_bundle_id") or ""))
        or not DEPLOYMENT_ID.fullmatch(str(payload.get("api_deployment_id") or ""))
        or not SCHEMA_REVISION.fullmatch(str(payload.get("schema_revision") or ""))
        or len(signing_key) < 32
    ):
        raise ValueError("rollback baseline runtime identity is invalid")
    signature = str(payload.get("signature") or "")
    unsigned = dict(payload)
    unsigned.pop("signature", None)
    expected = hmac.new(
        signing_key,
        canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    if (
        not signature.startswith("hmac-sha256:")
        or not hmac.compare_digest(
            signature.removeprefix("hmac-sha256:"),
            expected,
        )
    ):
        raise ValueError("rollback baseline runtime signature is invalid")
    return payload


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _write_job_env(path: Path, prefix: str, payload: dict[str, Any]) -> None:
    clean = str(prefix or "").strip().upper()
    if not clean or not clean.replace("_", "").isalnum():
        raise ValueError("job environment prefix is invalid")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in sorted(payload.items()):
            if key in {"schema", "passed"}:
                continue
            handle.write(f"{clean}{key.upper()}={value}\n")


def capture(args: argparse.Namespace) -> dict[str, Any]:
    database_url = _database_url(args.database_url_env)
    activation = _load_active_release(
        database_url,
        target_source_sha=args.target_source_sha,
    )
    if activation.get("phase") not in {
        "RESERVED",
        "ROLLBACK_BASELINE_VERIFIED",
    }:
        raise ValueError("rollback baseline can only be captured before target staging")
    expected_source = str(args.expected_source_sha or "").strip().lower()
    expected_runtime = str(args.expected_runtime_bundle_id or "").strip().lower()
    expected_deployment = str(args.expected_deployment_id or "").strip()
    if (
        not SOURCE_SHA.fullmatch(expected_source)
        or not RUNTIME_ID.fullmatch(expected_runtime)
        or not DEPLOYMENT_ID.fullmatch(expected_deployment)
    ):
        raise ValueError("expected rollback baseline coordinates are invalid")

    base_url = _exact_https_origin(args.base_url)
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        version_response = client.get(f"{base_url}/api/v1/version")
        if (
            version_response.status_code != 200
            or version_response.history
        ):
            raise ValueError("rollback baseline version endpoint is unavailable")
        version = version_response.json()
        if not isinstance(version, dict):
            raise ValueError("rollback baseline version payload is invalid")
        expected_schema = str(version.get("schema_revision") or "")
        if (
            version.get("source_sha") != expected_source
            or version.get("runtime_bundle_id") != expected_runtime
            or version.get("deployment_id") != expected_deployment
            or version.get("release_role") != "SAFE_BASELINE"
            or version.get("runtime_environment") != "production"
            or not SCHEMA_REVISION.fullmatch(expected_schema)
        ):
            raise ValueError("formal domain is not the expected rollback baseline")
        signing_key = os.environ.get(args.signing_key_env, "").encode()
        report = collect_api_runtime_coordinate_report(
            client,
            base_url=base_url,
            expected_deployment_id=expected_deployment,
            expected_runtime_bundle_id=expected_runtime,
            expected_source_sha=expected_source,
            expected_schema=expected_schema,
            expected_release_role="SAFE_BASELINE",
            expected_runtime_environment="production",
            bypass_secret="",
            signing_key=signing_key,
        )
    _verify_signed_runtime(report, signing_key=signing_key)
    report_path = Path(args.runtime_report)
    _write_once(report_path, report)
    raw = report_path.read_bytes()

    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    prefix = str(activation["private_evidence_prefix"]).strip("/\\")
    object_key = f"{prefix}/rollback-baseline-runtime.json"
    stored = store.put_create_once(object_key, raw)
    return {
        "schema": "vowpic.rollback-baseline-capture.v1",
        "passed": True,
        "target_source_sha": activation["source_sha"],
        "baseline_source_sha": report["source_sha"],
        "baseline_runtime_bundle_id": report["runtime_bundle_id"],
        "baseline_deployment_id": report["api_deployment_id"],
        "baseline_schema_revision": report["schema_revision"],
        "baseline_release_role": report["release_role"],
        "baseline_report_sha256": hashlib.sha256(raw).hexdigest(),
        "storage_state": stored.state,
    }


def read(args: argparse.Namespace) -> dict[str, Any]:
    database_url = _database_url(args.database_url_env)
    activation = _load_active_release(
        database_url,
        target_source_sha=args.target_source_sha,
    )
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(database_url) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT phase_rank, report_sha256, private_object_key,
                       coordinates_json
                FROM release_phase_evidence
                WHERE release_activation_id=%s
                  AND phase='ROLLBACK_BASELINE_VERIFIED'
                """,
                (activation["id"],),
            )
            row = cursor.fetchone()
    if row is None or int(row["phase_rank"]) != 1:
        raise ValueError("rollback baseline phase evidence is missing")

    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    phase_raw = store.read(str(row["private_object_key"]))
    if hashlib.sha256(phase_raw).hexdigest() != row["report_sha256"]:
        raise ValueError("rollback baseline phase report hash drift")
    phase = json.loads(phase_raw)
    evidence = (
        phase.get("phase_evidence", {})
        .get("evidence", {})
        .get("inspect-report", {})
    )
    expected_report_sha = str(evidence.get("sha256") or "")
    if (
        phase.get("phase") != "ROLLBACK_BASELINE_VERIFIED"
        or evidence.get("kind") != "file"
        or not re.fullmatch(r"[0-9a-f]{64}", expected_report_sha)
    ):
        raise ValueError("rollback baseline evidence binding is invalid")

    prefix = str(activation["private_evidence_prefix"]).strip("/\\")
    runtime_raw = store.read(f"{prefix}/rollback-baseline-runtime.json")
    runtime_sha = hashlib.sha256(runtime_raw).hexdigest()
    if runtime_sha != expected_report_sha:
        raise ValueError("rollback baseline runtime report hash drift")
    runtime = _verify_signed_runtime(
        json.loads(runtime_raw),
        signing_key=os.environ.get(args.signing_key_env, "").encode(),
    )
    coordinates = row["coordinates_json"]
    if isinstance(coordinates, str):
        coordinates = json.loads(coordinates)
    baseline_url = _exact_https_origin(coordinates.get("deployment_url"))
    if (
        runtime["api_deployment_id"] != coordinates.get("deployment_id")
        or runtime["api_deployment_id"] == activation.get("api_deployment_id")
    ):
        raise ValueError("rollback baseline deployment identity drift")
    payload = {
        "schema": "vowpic.rollback-baseline-resolution.v1",
        "passed": True,
        "target_source_sha": activation["source_sha"],
        "target_runtime_bundle_id": activation["runtime_bundle_id"],
        "target_deployment_id": activation["api_deployment_id"],
        "baseline_deployment_url": baseline_url,
        "baseline_deployment_id": runtime["api_deployment_id"],
        "baseline_source_sha": runtime["source_sha"],
        "baseline_runtime_bundle_id": runtime["runtime_bundle_id"],
        "baseline_schema_revision": runtime["schema_revision"],
        "baseline_release_role": runtime["release_role"],
        "baseline_report_sha256": runtime_sha,
    }
    if args.job_env:
        _write_job_env(Path(args.job_env), args.env_prefix, payload)
    return payload


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--base-url", required=True)
    capture_parser.add_argument("--expected-deployment-id", required=True)
    capture_parser.add_argument("--expected-runtime-bundle-id", required=True)
    capture_parser.add_argument("--expected-source-sha", required=True)
    capture_parser.add_argument("--runtime-report", required=True)
    read_parser = commands.add_parser("read")
    read_parser.add_argument("--job-env")
    read_parser.add_argument("--env-prefix", default="ROLLBACK_")
    for command in commands.choices.values():
        command.add_argument("--target-source-sha", required=True)
        command.add_argument(
            "--database-url-env",
            default="PRODUCTION_MIGRATION_DATABASE_URL",
        )
        command.add_argument(
            "--private-evidence-store-id-env",
            default="PRIVATE_EVIDENCE_STORE_ID",
        )
        command.add_argument(
            "--private-evidence-token-env",
            default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN",
        )
        command.add_argument(
            "--signing-key-env",
            default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
        )
        command.add_argument("--output", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        payload = capture(args) if args.command == "capture" else read(args)
        _write_once(Path(args.output), payload)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
