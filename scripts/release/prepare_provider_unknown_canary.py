#!/usr/bin/env python3
"""Prepare a signed, one-submit Provider unknown-state canary from real queued facts."""

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


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest


SIGNATURE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME = re.compile(r"^rtb_[0-9a-f]{64}$")


def canonical(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical(value[key])}"
            for key in sorted(value)
        ) + "}"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if not raw or len(raw) > 1_000_000 or path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be one bounded regular file")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, raw


def verify_acceptance_report(payload: dict[str, Any], *, signing_key: bytes) -> None:
    if len(signing_key) < 32:
        raise ValueError("acceptance evidence signing key is unavailable")
    match = SIGNATURE.fullmatch(str(payload.get("signature") or ""))
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    wanted = hmac.new(
        signing_key, canonical(unsigned).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), wanted):
        raise ValueError("queued Provider canary report signature is invalid")
    if (
        payload.get("schema") != "vowpic.linked-commercial-acceptance.v1"
        or payload.get("phase") != "queue-provider-unknown-state"
        or payload.get("passed") is not True
        or not isinstance(payload.get("links"), dict)
        or not isinstance(payload.get("assertions"), dict)
    ):
        raise ValueError("queued Provider canary report identity is invalid")


def _validate_dispatch_report(payload: dict[str, Any], activation: dict[str, Any]) -> None:
    coordinates = payload.get("coordinates")
    if (
        payload.get("schema") != "vowpic.worker-host-adapter-report.v1"
        or payload.get("action") != "set-dispatch"
        or payload.get("passed") is not True
        or not isinstance(coordinates, dict)
        or coordinates.get("runtime_bundle_id") != activation.get("runtime_bundle_id")
        or coordinates.get("worker_deployment_id") != activation.get("worker_deployment_id")
        or coordinates.get("dispatch_mode") != "disabled"
    ):
        raise ValueError("Provider canary requires an exact dispatch-disabled report")


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("Provider canary database URL is invalid")
    return clean


def read_queued_facts(
    database_url: str,
    *,
    source_sha: str,
    links: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
                  AND source_sha = %s AND phase = 'WORKER_DISPATCH_ENABLED'
                ORDER BY updated_at DESC LIMIT 2
                """,
                (source_sha,),
            )
            activations = [dict(row) for row in cursor.fetchall()]
            if len(activations) != 1:
                raise ValueError("exactly one dispatch-enabled release is required")
            cursor.execute(
                """
                SELECT o.id AS order_id, o.reservation_id, o.generation_job_id,
                       o.status AS order_status,
                       j.id AS job_id, j.submission_correlation_id,
                       j.status AS job_status, j.api_deployment_id,
                       j.runtime_bundle_id, j.expected_worker_image_digest,
                       a.id AS attempt_id, a.status AS attempt_status,
                       a.client_request_id, a.provider_job_id,
                       a.submission_accounting_state
                FROM orders o
                JOIN generation_jobs j ON j.id = o.generation_job_id
                JOIN generation_attempts a ON a.job_id = j.id
                WHERE o.id = %s AND o.reservation_id = %s
                  AND j.id = %s AND a.id = %s
                  AND j.submission_correlation_id = %s
                """,
                (
                    links.get("order_id"),
                    links.get("reservation_id"),
                    links.get("job_id"),
                    links.get("attempt_id"),
                    links.get("client_correlation_id"),
                ),
            )
            facts = [dict(row) for row in cursor.fetchall()]
    if len(facts) != 1:
        raise ValueError("queued Provider canary facts are missing or ambiguous")
    return activations[0], facts[0]


def build_canary_report(
    *,
    activation: dict[str, Any],
    queued_report: dict[str, Any],
    queued_report_sha256: str,
    dispatch_report: dict[str, Any],
    facts: dict[str, Any],
    manifest_sha256: str,
    signing_key: bytes,
    produced_at: datetime | None = None,
) -> dict[str, Any]:
    links = queued_report["links"]
    expected_fault_id = "afi_" + hashlib.sha256(
        (
            f"{activation['id']}:{activation['workflow_run_id']}:"
            f"{activation['workflow_attempt']}:provider-response-drop"
        ).encode("utf-8")
    ).hexdigest()[:32]
    if links.get("fault_intent_id") != expected_fault_id:
        raise ValueError("queued Provider canary fault intent ID mismatch")
    expected_facts = {
        "order_id": links.get("order_id"),
        "reservation_id": links.get("reservation_id"),
        "job_id": links.get("job_id"),
        "attempt_id": links.get("attempt_id"),
        "submission_correlation_id": links.get("client_correlation_id"),
        "api_deployment_id": activation.get("api_deployment_id"),
        "runtime_bundle_id": activation.get("runtime_bundle_id"),
        "expected_worker_image_digest": activation.get("worker_image_digest"),
    }
    for key, value in expected_facts.items():
        if str(facts.get(key)) != str(value):
            raise ValueError(f"queued Provider canary {key} mismatch")
    if (
        str(facts.get("order_status")) != "QUEUED"
        or str(facts.get("job_status")) != "QUEUED"
        or str(facts.get("attempt_status")) != "PREPARED"
        or facts.get("provider_job_id") is not None
        or str(facts.get("submission_accounting_state")) != "NOT_CAPTURED"
        or str(facts.get("client_request_id"))
        != str(facts.get("submission_correlation_id"))
    ):
        raise ValueError("queued Provider canary is not at the pre-submit boundary")
    _validate_dispatch_report(dispatch_report, activation)
    if manifest_sha256 != activation.get("manifest_sha256"):
        raise ValueError("queued Provider canary manifest mismatch")
    cost_cap = queued_report.get("cost_cap_minor_units")
    if not isinstance(cost_cap, int) or cost_cap < 1:
        raise ValueError("queued Provider canary cost cap is invalid")
    unsigned = {
        "schema": "vowpic.provider-unknown-canary.v1",
        "passed": True,
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": manifest_sha256,
        "worker_deployment_id": activation["worker_deployment_id"],
        "worker_image_digest": activation["worker_image_digest"],
        "fault_intent_id": expected_fault_id,
        "queued_report_sha256": queued_report_sha256,
        "queued_facts_sha256": hashlib.sha256(
            canonical({key: str(value) for key, value in sorted(expected_facts.items())}).encode("utf-8")
        ).hexdigest(),
        "dispatch_paused": True,
        "max_provider_submits": 1,
        "max_cost_minor_units": cost_cap,
        "produced_at": (produced_at or datetime.now(timezone.utc)).isoformat(),
    }
    signature = hmac.new(
        signing_key, canonical(unsigned).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {**unsigned, "signature": f"hmac-sha256:{signature}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-report", required=True)
    parser.add_argument("--worker-dispatch-report", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--signing-key-env", default="ACCEPTANCE_EVIDENCE_SIGNING_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        signing_key = os.environ.get(args.signing_key_env, "").encode("utf-8")
        queued, queued_raw = _read_json(Path(args.order_report), label="queued order report")
        verify_acceptance_report(queued, signing_key=signing_key)
        dispatch, _ = _read_json(
            Path(args.worker_dispatch_report), label="Worker dispatch report"
        )
        manifest_path = Path(args.manifest)
        manifest_raw = manifest_path.read_bytes()
        manifest = validate_manifest(json.loads(manifest_raw.decode("utf-8")))
        if canonical_manifest_bytes(manifest) != manifest_raw:
            raise ValueError("Provider canary manifest is not canonical")
        manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
        if manifest_sha != str(args.expected_manifest_sha256 or "").strip().lower():
            raise ValueError("Provider canary manifest SHA-256 mismatch")
        activation, facts = read_queued_facts(
            os.environ.get(args.database_url_env, ""),
            source_sha=str(queued.get("source_sha") or ""),
            links=queued["links"],
        )
        report = build_canary_report(
            activation=activation,
            queued_report=queued,
            queued_report_sha256=hashlib.sha256(queued_raw).hexdigest(),
            dispatch_report=dispatch,
            facts=facts,
            manifest_sha256=manifest_sha,
            signing_key=signing_key,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(report) + "\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
