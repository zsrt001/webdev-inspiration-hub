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

COORDINATE_KINDS = (
    "preview-identity",
    "preview-commercial",
    "preview-commercial-cleaned",
    "safe-baseline",
    "commercial-7a",
    "contract-7b",
)
# Backward-compatible name for callers that only use it as argparse choices.
INITIAL_COORDINATE_KINDS = COORDINATE_KINDS
SPEC_BY_KIND = {
    "preview-identity": {"environment": "preview", "kind": "PREVIEW_IDENTITY", "phase": "COMPLETED"},
    "preview-commercial": {
        "environment": "preview", "kind": "PREVIEW_COMMERCIAL", "phase": "COMPLETED"
    },
    "preview-commercial-cleaned": {
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "phase": "CLEANED",
        "report_phase": "COMPLETED",
    },
    "safe-baseline": {"environment": "production", "kind": "SAFE_BASELINE_INSTALL", "phase": "COMPLETED"},
    "commercial-7a": {"environment": "production", "kind": "COMMERCIAL_7A", "phase": "COMPLETED"},
    "contract-7b": {"environment": "production", "kind": "CONTRACT_7B", "phase": "COMPLETED"},
}
FORBIDDEN_ACTIVATION_KEYS = {
    "caller_pass", "caller_deployment_id", "caller_manifest_sha256", "caller_report_sha256"
}
OUTPUT_KEYS = (
    "activation_id", "environment", "kind", "source_sha", "runtime_bundle_id",
    "api_deployment_id", "api_deployment_url", "api_role",
    "worker_deployment_id", "worker_role", "worker_image_digest", "manifest_sha256",
    "report_sha256", "private_evidence_prefix", "workflow_run_id", "workflow_attempt", "phase",
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
    if coordinate_kind != "safe-baseline":
        required = (*required, "id", "api_deployment_url")
    commercial_kinds = {
        "preview-commercial", "preview-commercial-cleaned", "commercial-7a", "contract-7b"
    }
    if coordinate_kind in commercial_kinds:
        required = (
            *required,
            "manifest_sha256", "api_role", "worker_deployment_id", "worker_role",
            "worker_image_digest", "private_evidence_prefix", "workflow_run_id", "workflow_attempt",
        )
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
        "phase": spec.get("report_phase", activation["phase"]),
    }
    if coordinate_kind in commercial_kinds:
        report_matches.update(
            {
                "manifest_sha256": activation["manifest_sha256"],
                "api_role": activation["api_role"],
                "worker_deployment_id": activation["worker_deployment_id"],
                "worker_role": activation["worker_role"],
                "worker_image_digest": activation["worker_image_digest"],
            }
        )
    for key, expected in report_matches.items():
        if report.get(key) != expected:
            raise ValueError(f"create-once report {key} mismatch")
    if coordinate_kind != "safe-baseline" and str(report.get("activation_id")) != str(activation["id"]):
        raise ValueError("create-once report activation ID mismatch")
    report_hash = report.get("_content_sha256") or report.get("sha256")
    if report_hash != activation["report_sha256"]:
        raise ValueError("create-once report hash mismatch")
    report_created = _timestamp(report.get("created_at"))
    if report_created > current + timedelta(minutes=5) or current - report_created > maximum_age:
        raise ValueError("create-once report is stale")
    resolved = {
        key: activation.get(key)
        for key in OUTPUT_KEYS
        if key != "activation_id" and activation.get(key) is not None
    }
    if activation.get("id") is not None:
        resolved["activation_id"] = str(activation["id"])
    return resolved


def _load_activation(
    database_url: str,
    coordinate_kind: str,
    expected_source_sha: str | None = None,
) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    spec = SPEC_BY_KIND[coordinate_kind]
    with psycopg2.connect(database_url) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
                SELECT id, environment, kind, source_sha, runtime_bundle_id, manifest_sha256,
                       report_sha256, api_deployment_id, api_deployment_url, worker_deployment_id,
                       api_role, worker_role, worker_image_digest, phase, private_evidence_prefix,
                       workflow_run_id, workflow_attempt, updated_at
                FROM release_activations
                WHERE environment = %s AND kind = %s AND phase = %s
                {source_filter}
                ORDER BY updated_at DESC
                LIMIT 2
                """
            parameters: tuple[object, ...] = (
                spec["environment"], spec["kind"], spec["phase"]
            )
            if expected_source_sha:
                query = query.format(source_filter="AND source_sha = %s")
                parameters = (*parameters, expected_source_sha)
            else:
                query = query.format(source_filter="")
            cursor.execute(query, parameters)
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
    if not prefix or ".." in Path(prefix).parts or "latest" in {part.lower() for part in Path(prefix).parts}:
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
    parser.add_argument("--coordinate-kind", required=True, choices=COORDINATE_KINDS)
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
    activations = _load_activation(
        os.environ[args.database_url_env], args.coordinate_kind, args.source_sha
    )
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
