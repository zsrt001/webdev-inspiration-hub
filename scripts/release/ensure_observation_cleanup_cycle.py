#!/usr/bin/env python3
"""Run or recover one exact signed cleanup cycle for a release observation."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.private_evidence_store import PrivateBlobEvidenceStore  # noqa: E402


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
COORDINATE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SUMMARY_FIELDS = {
    "source_images": {
        "orders",
        "deleted_assets",
        "pending_assets",
        "failed_assets",
        "legacy_blocked_orders",
    },
    "orders": {
        "orders",
        "deleted_assets",
        "pending_assets",
        "failed_assets",
        "legacy_blocked_orders",
    },
    "deletion": {
        "rechecked",
        "claimed",
        "deleted",
        "not_found",
        "failed",
        "tombstones_reconciled",
    },
}
REPORT_FIELDS = {
    "schema",
    "passed",
    "observation_run_id",
    "source_sha",
    "runtime_bundle_id",
    "api_deployment_id",
    "response_sha256",
    "counts",
    "observed_at",
    "signature",
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


def _exact_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("cleanup cycle requires one exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _validate_counts(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    if not isinstance(payload, dict) or set(payload) != {
        "success",
        *SUMMARY_FIELDS,
    } or payload.get("success") is not True:
        raise ValueError("cleanup endpoint response fields are invalid")
    result: dict[str, dict[str, int]] = {}
    for section, fields in SUMMARY_FIELDS.items():
        value = payload.get(section)
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or any(type(value[field]) is not int or value[field] < 0 for field in fields)
        ):
            raise ValueError(f"cleanup endpoint {section} counts are invalid")
        result[section] = dict(value)
    if (
        result["source_images"]["failed_assets"] != 0
        or result["source_images"]["legacy_blocked_orders"] != 0
        or result["orders"]["failed_assets"] != 0
        or result["orders"]["legacy_blocked_orders"] != 0
        or result["deletion"]["failed"] != 0
    ):
        raise ValueError("cleanup cycle reported a blocking failure")
    return result


def validate_cleanup_report(
    payload: dict[str, Any],
    *,
    run: dict[str, Any],
    signing_key: bytes,
) -> None:
    expected = {
        "schema": "vowpic.observation-cleanup-cycle.v1",
        "passed": True,
        "observation_run_id": str(run["id"]),
        "source_sha": run["source_sha"],
        "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != REPORT_FIELDS
        or any(payload.get(field) != value for field, value in expected.items())
        or not SHA64.fullmatch(str(payload.get("response_sha256") or ""))
        or len(signing_key) < 32
    ):
        raise ValueError("cleanup cycle report coordinates are invalid")
    _validate_counts({"success": True, **payload["counts"]})
    observed = datetime.fromisoformat(
        str(payload["observed_at"]).replace("Z", "+00:00")
    )
    if observed.tzinfo is None:
        raise ValueError("cleanup cycle timestamp is not timezone-aware")
    unsigned = dict(payload)
    signature = str(unsigned.pop("signature"))
    wanted = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    if (
        not signature.startswith("hmac-sha256:")
        or not hmac.compare_digest(signature.removeprefix("hmac-sha256:"), wanted)
    ):
        raise ValueError("cleanup cycle report signature is invalid")


def _load_run(database_url: str, observation_run_id: str) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT observation.id, observation.state,
                       observation.cleanup_cycle_sha256,
                       activation.source_sha, activation.runtime_bundle_id,
                       activation.api_deployment_id,
                       activation.private_evidence_prefix
                FROM release_observation_runs AS observation
                JOIN release_activations AS activation
                  ON activation.id = observation.release_activation_id
                WHERE observation.id = %s
                  AND activation.environment = 'production'
                  AND activation.kind = 'COMMERCIAL_7A'
                """,
                (observation_run_id,),
            )
            row = cursor.fetchone()
    if row is None or row["state"] != "OBSERVING":
        raise ValueError("cleanup cycle observation is not active")
    return dict(row)


def ensure_cleanup_cycle(args: argparse.Namespace) -> dict[str, Any]:
    run = _load_run(
        os.environ.get(args.database_url_env, ""), args.observation_run_id
    )
    if (
        not SHA40.fullmatch(args.expected_source_sha)
        or not RUNTIME_ID.fullmatch(args.expected_runtime_bundle_id)
        or not COORDINATE.fullmatch(args.expected_api_deployment_id)
        or args.expected_source_sha != run["source_sha"]
        or args.expected_runtime_bundle_id != run["runtime_bundle_id"]
        or args.expected_api_deployment_id != run["api_deployment_id"]
    ):
        raise ValueError("cleanup cycle expected coordinates drifted")
    signing_key = os.environ.get(args.signing_key_env, "").encode()
    store = PrivateBlobEvidenceStore(
        store_id=os.environ.get(args.private_evidence_store_id_env, ""),
        token=os.environ.get(args.private_evidence_token_env, ""),
    )
    prefix = str(run["private_evidence_prefix"]).strip("/\\")
    object_key = f"{prefix}/observations/{run['id']}/cleanup-cycle.json"
    try:
        existing = store.read(object_key)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        digest = hashlib.sha256(existing).hexdigest()
        payload = json.loads(existing)
        validate_cleanup_report(payload, run=run, signing_key=signing_key)
        if run["cleanup_cycle_sha256"] not in {None, digest}:
            raise ValueError("cleanup cycle durable hash drifted")
        return payload
    if run["cleanup_cycle_sha256"] is not None:
        raise ValueError("cleanup cycle database hash has no Private Blob object")
    cleanup_token = os.environ.get(args.cleanup_token_env, "").strip()
    if len(cleanup_token) < 32 or len(signing_key) < 32:
        raise ValueError("cleanup cycle credentials are unavailable")
    origin = _exact_origin(args.base_url)
    with httpx.Client(timeout=300.0, follow_redirects=False) as client:
        response = client.post(
            f"{origin}/api/v1/ops/cleanup_expired_assets",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {cleanup_token}",
            },
        )
    if response.history or response.status_code != 200:
        raise ValueError("cleanup cycle endpoint did not return HTTP 200")
    try:
        response_payload = response.json()
    except ValueError as exc:
        raise ValueError("cleanup cycle endpoint returned invalid JSON") from exc
    counts = _validate_counts(response_payload)
    unsigned = {
        "schema": "vowpic.observation-cleanup-cycle.v1",
        "passed": True,
        "observation_run_id": str(run["id"]),
        "source_sha": run["source_sha"],
        "runtime_bundle_id": run["runtime_bundle_id"],
        "api_deployment_id": run["api_deployment_id"],
        "response_sha256": hashlib.sha256(response.content).hexdigest(),
        "counts": counts,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    signature = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
    validate_cleanup_report(report, run=run, signing_key=signing_key)
    store.put_create_once(object_key, _canonical(report) + b"\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--observation-run-id", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-runtime-bundle-id", required=True)
    parser.add_argument("--expected-api-deployment-id", required=True)
    parser.add_argument("--database-url-env", default="OBSERVATION_READ_DATABASE_URL")
    parser.add_argument("--cleanup-token-env", default="CLEANUP_CRON_TOKEN")
    parser.add_argument("--signing-key-env", default="OBSERVATION_SIGNING_KEY")
    parser.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    parser.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_READ_WRITE_TOKEN")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = ensure_cleanup_cycle(args)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
