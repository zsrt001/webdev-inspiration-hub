#!/usr/bin/env python3
"""Resolve one cleaned Preview activation by exact workflow and package coordinates."""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.resolve_release_coordinates import (  # noqa: E402
    OUTPUT_KEYS,
    _read_report,
    resolve_records,
)


_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")


def validate_expected_binding(
    *,
    source_sha: object,
    workflow_run_id: object,
    workflow_attempt: object,
    activation_id: object,
    runtime_bundle_id: object,
    api_deployment_id: object,
    manifest_sha256: object,
) -> dict[str, Any]:
    source = str(source_sha or "").strip().lower()
    run_id = str(workflow_run_id or "").strip()
    try:
        attempt = int(workflow_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError("Preview workflow attempt is invalid") from exc
    activation = str(activation_id or "").strip().lower()
    runtime = str(runtime_bundle_id or "").strip().lower()
    deployment = str(api_deployment_id or "").strip()
    manifest = str(manifest_sha256 or "").strip().lower()
    try:
        if str(UUID(activation)) != activation:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError("Preview activation ID is invalid") from exc
    if not _SOURCE_SHA.fullmatch(source):
        raise ValueError("Preview source SHA is invalid")
    if not _RUN_ID.fullmatch(run_id) or not 1 <= attempt <= 2_147_483_647:
        raise ValueError("Preview workflow coordinates are invalid")
    if not _RUNTIME_ID.fullmatch(runtime):
        raise ValueError("Preview runtime bundle ID is invalid")
    if not _DEPLOYMENT_ID.fullmatch(deployment):
        raise ValueError("Preview API deployment ID is invalid")
    if not _SHA256.fullmatch(manifest):
        raise ValueError("Preview manifest SHA-256 is invalid")
    return {
        "activation_id": activation,
        "source_sha": source,
        "workflow_run_id": run_id,
        "workflow_attempt": attempt,
        "runtime_bundle_id": runtime,
        "api_deployment_id": deployment,
        "manifest_sha256": manifest,
    }


def select_exact_activation(
    activations: list[dict[str, Any]],
    *,
    expected: dict[str, Any],
) -> dict[str, Any]:
    matches = [
        dict(row)
        for row in activations
        if row.get("environment") == "preview"
        and row.get("kind") == "PREVIEW_COMMERCIAL"
        and row.get("phase") == "CLEANED"
        and str(row.get("source_sha") or "") == expected["source_sha"]
        and str(row.get("workflow_run_id") or "") == expected["workflow_run_id"]
        and int(row.get("workflow_attempt") or 0) == expected["workflow_attempt"]
    ]
    if len(matches) != 1:
        raise ValueError(
            "exactly one cleaned Preview activation must match the workflow attempt"
        )
    row = matches[0]
    actual = {
        "activation_id": str(row.get("id") or "").lower(),
        "source_sha": str(row.get("source_sha") or "").lower(),
        "workflow_run_id": str(row.get("workflow_run_id") or ""),
        "workflow_attempt": int(row.get("workflow_attempt") or 0),
        "runtime_bundle_id": str(row.get("runtime_bundle_id") or "").lower(),
        "api_deployment_id": str(row.get("api_deployment_id") or ""),
        "manifest_sha256": str(row.get("manifest_sha256") or "").lower(),
    }
    if actual != expected:
        drift = sorted(key for key in expected if actual.get(key) != expected[key])
        raise ValueError(
            "Preview activation differs from the verified package: " + ", ".join(drift)
        )
    return row


def resolve_exact_records(
    activations: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    expected: dict[str, Any],
    maximum_age: timedelta = timedelta(hours=2),
) -> dict[str, Any]:
    activation = select_exact_activation(activations, expected=expected)
    resolved = resolve_records(
        "preview-commercial-cleaned",
        [activation],
        report,
        maximum_age=maximum_age,
        expected_source_sha=expected["source_sha"],
    )
    for key, value in expected.items():
        resolved_key = "activation_id" if key == "activation_id" else key
        if resolved.get(resolved_key) != value:
            raise ValueError(f"resolved Preview {key} differs from the package")
    return resolved


def _database_url(value: object) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("Preview coordinate database URL is invalid")
    return clean


def load_exact_activations(
    database_url: str,
    *,
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, environment, kind, source_sha, runtime_bundle_id,
                       manifest_sha256, report_sha256, api_deployment_id,
                       api_deployment_url, worker_deployment_id, api_role,
                       worker_role, worker_image_digest, phase,
                       private_evidence_prefix, workflow_run_id,
                       workflow_attempt, updated_at
                FROM release_activations
                WHERE environment = 'preview'
                  AND kind = 'PREVIEW_COMMERCIAL'
                  AND phase = 'CLEANED'
                  AND source_sha = %s
                  AND workflow_run_id = %s
                  AND workflow_attempt = %s
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (
                    expected["source_sha"],
                    expected["workflow_run_id"],
                    expected["workflow_attempt"],
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        connection.rollback()
    return rows


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _write_job_env(path: Path, prefix: str, resolved: dict[str, Any]) -> None:
    clean_prefix = prefix.strip().upper()
    if not clean_prefix or not clean_prefix.replace("_", "").isalnum():
        raise ValueError("Preview environment prefix is invalid")
    inherited = [
        f"{clean_prefix}{key.upper()}"
        for key in OUTPUT_KEYS
        if os.environ.get(f"{clean_prefix}{key.upper()}")
    ]
    if inherited:
        raise ValueError(
            "inherited Preview coordinates are forbidden: "
            + ", ".join(sorted(inherited))
        )
    lines = [
        f"{clean_prefix}{key.upper()}={value}"
        for key, value in sorted(resolved.items())
    ]
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--runtime-bundle-id", required=True)
    parser.add_argument("--api-deployment-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--maximum-age-seconds", type=int, default=7200)
    parser.add_argument("--output", required=True)
    parser.add_argument("--job-env")
    parser.add_argument("--env-prefix", default="PREVIEW_")
    args = parser.parse_args()
    try:
        expected = validate_expected_binding(
            source_sha=args.source_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            activation_id=args.activation_id,
            runtime_bundle_id=args.runtime_bundle_id,
            api_deployment_id=args.api_deployment_id,
            manifest_sha256=args.manifest_sha256,
        )
        database_url = os.environ.get(args.database_url_env, "")
        activations = load_exact_activations(database_url, expected=expected)
        activation = select_exact_activation(activations, expected=expected)
        report = _read_report(
            activation,
            None,
            github_token=os.environ.get(args.github_token_env, "").strip(),
        )
        resolved = resolve_exact_records(
            activations,
            report,
            expected=expected,
            maximum_age=timedelta(seconds=max(1, args.maximum_age_seconds)),
        )
        _write_create_once(Path(args.output), resolved)
        if args.job_env:
            _write_job_env(Path(args.job_env), args.env_prefix, resolved)
        print(
            json.dumps(
                {
                    "state": "RESOLVED_EXACT_PREVIEW_ATTEMPT",
                    "activation_id": resolved["activation_id"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
