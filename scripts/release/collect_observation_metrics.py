#!/usr/bin/env python3
"""Collect one real, sanitized COMMERCIAL_7A observation metrics input."""

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
from urllib.parse import quote

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.ensure_observation_cleanup_cycle import (  # noqa: E402
    validate_cleanup_report,
)
from scripts.release.observe_release import validate_metric_values  # noqa: E402


DATABASE_METRIC_FIELDS = {
    "unhandled_signed_webhooks",
    "ledger_reconciliation_failures",
    "oldest_mandatory_outbox_age_seconds",
    "synthetic_flow_dlq",
    "acceptance_prefix_deletion_failures",
    "rls_policy_gap_count",
    "legacy_identity_fallback_count",
    "flag_bundle_drift",
}
WORKER_REPORT_FIELDS = {
    "schema",
    "passed",
    "action",
    "contract_sha256",
    "request_sha256",
    "host_response_sha256",
    "state",
    "coordinates",
    "observed_at",
    "signature",
}
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BLOCKING_LABELS = {
    "p0",
    "p1",
    "priority:p0",
    "priority:p1",
    "severity:p0",
    "severity:p1",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("observation reader database URL is invalid")
    return clean


def _bounded_json(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    report_path = Path(path)
    if (
        not report_path.is_file()
        or report_path.is_symlink()
        or report_path.stat().st_size <= 0
        or report_path.stat().st_size > 1_000_000
    ):
        raise ValueError(f"{label} must be one bounded regular JSON file")
    raw = report_path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, raw


def _database_metrics(database_url: str, run_id: str) -> dict[str, int]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM public.read_release_observation_metrics_v1(%s)",
                (run_id,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1 or set(rows[0]) != DATABASE_METRIC_FIELDS:
        raise ValueError("observation database metric contract is unavailable")
    result: dict[str, int] = {}
    for field, value in rows[0].items():
        if type(value) is not int or value < 0:
            raise ValueError(f"observation database metric is invalid: {field}")
        result[field] = value
    return result


def _unresolved_p0_p1(*, repository: str, token: str) -> int:
    if not REPOSITORY.fullmatch(repository) or len(token) < 20:
        raise ValueError("GitHub incident reader configuration is invalid")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    blocking = 0
    with httpx.Client(
        base_url="https://api.github.com",
        headers=headers,
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        for required_label in ("severity:p0", "severity:p1"):
            label_response = client.get(
                f"/repos/{repository}/labels/{quote(required_label, safe='')}"
            )
            if (
                label_response.history
                or label_response.status_code != 200
                or not isinstance(label_response.json(), dict)
                or label_response.json().get("name") != required_label
            ):
                raise ValueError(
                    "GitHub incident severity label contract is missing"
                )
        for page in range(1, 11):
            response = client.get(
                f"/repos/{repository}/issues",
                params={
                    "state": "open",
                    "per_page": 100,
                    "page": page,
                    "sort": "created",
                    "direction": "asc",
                },
            )
            if response.history or response.status_code != 200:
                raise ValueError("GitHub incident query failed")
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub incident query returned invalid JSON")
            for issue in payload:
                if not isinstance(issue, dict) or "pull_request" in issue:
                    continue
                labels = issue.get("labels")
                if not isinstance(labels, list):
                    raise ValueError("GitHub incident labels are invalid")
                names = {
                    str(label.get("name") or "").strip().lower()
                    for label in labels
                    if isinstance(label, dict)
                }
                if names & BLOCKING_LABELS:
                    blocking += 1
            if len(payload) < 100:
                return blocking
    raise ValueError("GitHub incident query exceeded the bounded page limit")


def _validate_worker_report(
    payload: dict[str, Any],
    *,
    signing_key: bytes,
    expected_runtime_bundle_id: str,
    expected_worker_deployment_id: str,
    expected_worker_image_digest: str,
) -> float:
    coordinates = payload.get("coordinates")
    if (
        set(payload) != WORKER_REPORT_FIELDS
        or payload.get("schema") != "vowpic.worker-host-adapter-report.v1"
        or payload.get("passed") is not True
        or payload.get("action") != "heartbeat"
        or not isinstance(coordinates, dict)
        or coordinates.get("runtime_bundle_id") != expected_runtime_bundle_id
        or coordinates.get("worker_deployment_id") != expected_worker_deployment_id
        or coordinates.get("worker_image_digest") != expected_worker_image_digest
        or len(signing_key) < 32
    ):
        raise ValueError("observation Worker heartbeat coordinates are invalid")
    unsigned = dict(payload)
    signature = str(unsigned.pop("signature"))
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if (
        not signature.startswith("hmac-sha256:")
        or not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted)
    ):
        raise ValueError("observation Worker heartbeat signature is invalid")
    observed = datetime.fromisoformat(
        str(payload["observed_at"]).replace("Z", "+00:00")
    )
    if observed.tzinfo is None:
        raise ValueError("observation Worker heartbeat timestamp is not timezone-aware")
    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        raise ValueError("observation Worker heartbeat is from the future")
    return age


def collect(args: argparse.Namespace) -> dict[str, Any]:
    database_metrics = _database_metrics(
        os.environ.get(args.database_url_env, ""), args.observation_run_id
    )
    worker, _ = _bounded_json(args.worker_report, label="Worker heartbeat report")
    cleanup, cleanup_raw = _bounded_json(
        args.cleanup_report, label="cleanup cycle report"
    )
    run = {
        "id": args.observation_run_id,
        "source_sha": args.expected_source_sha,
        "runtime_bundle_id": args.expected_runtime_bundle_id,
        "api_deployment_id": args.expected_api_deployment_id,
    }
    observation_key = os.environ.get(args.observation_signing_key_env, "").encode()
    validate_cleanup_report(cleanup, run=run, signing_key=observation_key)
    worker_age = _validate_worker_report(
        worker,
        signing_key=os.environ.get(args.worker_signing_key_env, "").encode(),
        expected_runtime_bundle_id=args.expected_runtime_bundle_id,
        expected_worker_deployment_id=args.expected_worker_deployment_id,
        expected_worker_image_digest=args.expected_worker_image_digest,
    )
    metrics: dict[str, Any] = {
        "unresolved_p0_p1": _unresolved_p0_p1(
            repository=os.environ.get(args.github_repository_env, ""),
            token=os.environ.get(args.github_token_env, ""),
        ),
        **database_metrics,
        "worker_heartbeat_age_seconds": round(worker_age, 3),
        "cleanup_status": "PASS",
        "cleanup_cycle_sha256": hashlib.sha256(cleanup_raw).hexdigest(),
    }
    validate_metric_values(metrics)
    return {
        "schema": "vowpic.observation-metrics-input.v1",
        "observation_run_id": args.observation_run_id,
        "source_sha": args.expected_source_sha,
        "metrics": metrics,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-run-id", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-runtime-bundle-id", required=True)
    parser.add_argument("--expected-api-deployment-id", required=True)
    parser.add_argument("--expected-worker-deployment-id", required=True)
    parser.add_argument("--expected-worker-image-digest", required=True)
    parser.add_argument("--worker-report", required=True)
    parser.add_argument("--cleanup-report", required=True)
    parser.add_argument("--database-url-env", default="OBSERVATION_READ_DATABASE_URL")
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--github-repository-env", default="GITHUB_REPOSITORY")
    parser.add_argument("--worker-signing-key-env", default="WORKER_HOST_EVIDENCE_SIGNING_KEY")
    parser.add_argument("--observation-signing-key-env", default="OBSERVATION_SIGNING_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = collect(args)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
