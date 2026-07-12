#!/usr/bin/env python3
"""Resolve fresh release coordinates from service-owned activation state."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.github_artifact_evidence import (
    REFERENCE_PREFIX as GITHUB_ARTIFACT_REFERENCE_PREFIX,
    read_report as read_github_artifact_report,
)

INITIAL_COORDINATE_KINDS = ("preview-identity", "safe-baseline")
SPEC_BY_KIND = {
    "preview-identity": {"environment": "preview", "kind": "PREVIEW_IDENTITY", "phase": "COMPLETED"},
    "safe-baseline": {"environment": "production", "kind": "SAFE_BASELINE_INSTALL", "phase": "COMPLETED"},
}
FORBIDDEN_ACTIVATION_KEYS = {
    "caller_pass", "caller_deployment_id", "caller_manifest_sha256", "caller_report_sha256"
}
OUTPUT_KEYS = (
    "environment", "kind", "source_sha", "runtime_bundle_id", "api_deployment_id",
    "worker_deployment_id", "worker_image_digest", "manifest_sha256", "report_sha256", "phase",
)


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("coordinate timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def resolve_records(
    coordinate_kind: str,
    activations: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    now: datetime | None = None,
    maximum_age: timedelta = timedelta(hours=2),
    expected_source_sha: str | None = None,
) -> dict[str, Any]:
    if coordinate_kind not in SPEC_BY_KIND:
        raise ValueError(f"coordinate kind is not allowlisted: {coordinate_kind}")
    if len(activations) != 1:
        raise ValueError("exactly one service-owned activation row is required")
    activation = dict(activations[0])
    if FORBIDDEN_ACTIVATION_KEYS & set(activation):
        raise ValueError("caller-authored authority claims are forbidden")
    spec = SPEC_BY_KIND[coordinate_kind]
    for key in ("environment", "kind", "phase"):
        if activation.get(key) != spec[key]:
            raise ValueError(f"activation {key} does not match {coordinate_kind}")
    required = ("source_sha", "runtime_bundle_id", "api_deployment_id", "report_sha256", "updated_at")
    missing = [key for key in required if not activation.get(key)]
    if missing:
        raise ValueError(f"activation coordinates are incomplete: {', '.join(missing)}")
    if expected_source_sha and activation["source_sha"] != expected_source_sha:
        raise ValueError("activation source SHA mismatch")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    updated_at = _timestamp(activation["updated_at"])
    if updated_at > current + timedelta(minutes=5) or current - updated_at > maximum_age:
        raise ValueError("activation coordinates are stale")

    report_matches = {
        "environment": activation["environment"],
        "kind": activation["kind"],
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "api_deployment_id": activation["api_deployment_id"],
        "phase": activation["phase"],
    }
    for key, expected in report_matches.items():
        if report.get(key) != expected:
            raise ValueError(f"create-once report {key} mismatch")
    report_hash = report.get("_content_sha256") or report.get("sha256")
    if report_hash != activation["report_sha256"]:
        raise ValueError("create-once report hash mismatch")
    report_created = _timestamp(report.get("created_at"))
    if report_created > current + timedelta(minutes=5) or current - report_created > maximum_age:
        raise ValueError("create-once report is stale")
    return {key: activation.get(key) for key in OUTPUT_KEYS if activation.get(key) is not None}


def _load_activation(database_url: str, coordinate_kind: str) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    spec = SPEC_BY_KIND[coordinate_kind]
    with psycopg2.connect(database_url) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT environment, kind, source_sha, runtime_bundle_id, manifest_sha256,
                       report_sha256, api_deployment_id, worker_deployment_id,
                       worker_image_digest, phase, private_evidence_prefix, updated_at
                FROM release_activations
                WHERE environment = %s AND kind = %s AND phase = %s
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (spec["environment"], spec["kind"], spec["phase"]),
            )
            return [dict(row) for row in cursor.fetchall()]


def _read_report(
    activation: dict[str, Any],
    private_store_root: Path | None,
    *,
    github_token: str = "",
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
    if prefix.startswith(GITHUB_ARTIFACT_REFERENCE_PREFIX):
        if not github_token.strip():
            raise ValueError("GitHub artifact evidence requires a read token")
        owns_client = http_client is None
        client = http_client or httpx.Client(timeout=20.0)
        try:
            raw, payload = read_github_artifact_report(
                prefix,
                token=github_token,
                client=client,
            )
        finally:
            if owns_client:
                client.close()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != activation.get("report_sha256"):
            raise ValueError("create-once report bytes do not match the activation hash")
        return {**payload, "_content_sha256": actual_sha256}
    if private_store_root is None:
        raise ValueError("local private evidence root is required")
    if not prefix or ".." in Path(prefix).parts:
        raise ValueError("activation private evidence prefix is invalid")
    root = private_store_root.resolve()
    report_path = (root / prefix / "activation-report.json").resolve()
    if root not in report_path.parents:
        raise ValueError("activation report escaped the private evidence root")
    raw = report_path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != activation.get("report_sha256"):
        raise ValueError("create-once report bytes do not match the activation hash")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("create-once report must be a JSON object")
    payload["_content_sha256"] = actual_sha256
    return payload


def _write_job_env(path: Path, prefix: str, resolved: dict[str, Any]) -> None:
    clean_prefix = prefix.strip().upper()
    if not clean_prefix or not clean_prefix.replace("_", "").isalnum():
        raise ValueError("job environment prefix is invalid")
    lines = [f"{clean_prefix}{key.upper()}={value}" for key, value in sorted(resolved.items())]
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def _reject_inherited_coordinates(prefix: str) -> None:
    clean_prefix = prefix.strip().upper()
    inherited = [f"{clean_prefix}{key.upper()}" for key in OUTPUT_KEYS if os.environ.get(f"{clean_prefix}{key.upper()}")]
    if inherited:
        raise ValueError(f"inherited release coordinates are forbidden: {', '.join(sorted(inherited))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinate-kind", required=True, choices=INITIAL_COORDINATE_KINDS)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--private-evidence-root-env")
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--source-sha")
    parser.add_argument("--maximum-age-seconds", type=int, default=7200)
    parser.add_argument("--output", required=True)
    parser.add_argument("--job-env")
    parser.add_argument("--env-prefix", default="RELEASE_")
    args = parser.parse_args()
    if args.database_url_env not in os.environ or not os.environ[args.database_url_env].strip():
        raise ValueError("database URL environment variable is absent")
    _reject_inherited_coordinates(args.env_prefix)
    activations = _load_activation(os.environ[args.database_url_env], args.coordinate_kind)
    if len(activations) != 1:
        raise ValueError("exactly one activation row is required")
    root_value = (
        os.environ.get(args.private_evidence_root_env, "").strip()
        if args.private_evidence_root_env
        else ""
    )
    report = _read_report(
        activations[0],
        Path(root_value) if root_value else None,
        github_token=os.environ.get(args.github_token_env, "").strip(),
    )
    resolved = resolve_records(
        args.coordinate_kind,
        activations,
        report,
        maximum_age=timedelta(seconds=max(1, args.maximum_age_seconds)),
        expected_source_sha=args.source_sha,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(resolved, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if args.job_env:
        _write_job_env(Path(args.job_env), args.env_prefix, resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
