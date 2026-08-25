#!/usr/bin/env python3
"""Restore the already-deployed COMMERCIAL_7A runtime to full public operation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg2
from psycopg2.extras import Json, RealDictCursor


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.apply_activation_plan import (  # noqa: E402
    CAPABILITIES,
    _database_url,
    _snapshot_hash,
)


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_BUNDLE = re.compile(r"^rtb_[0-9a-f]{64}$")
DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")
EXPECTED_SCHEMA = "20260710_0021"
EXPECTED_RELEASE_ROLE = "COMMERCIAL_7A"


def _load_json(response: httpx.Response, *, label: str) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} response must be an object")
    return payload


def _runtime_coordinates(
    client: httpx.Client,
    *,
    base_url: str,
    expected_source_sha: str,
    expected_runtime_bundle_id: str,
    expected_deployment_id: str,
) -> dict[str, Any]:
    ready = _load_json(client.get(f"{base_url}/health/ready"), label="readiness")
    checks = ready.get("checks")
    if (
        ready.get("ready") is not True
        or ready.get("strict_mode") is not True
        or ready.get("blockers") != []
        or not isinstance(checks, dict)
        or (checks.get("database_schema") or {}).get("detail") != EXPECTED_SCHEMA
        or (checks.get("database_role") or {}).get("detail")
        != "vowpic_app_runtime:vowpic_runtime"
        or (checks.get("control_plane_database") or {}).get("detail")
        != "vowpic_control_writer_login:vowpic_control_writer"
    ):
        raise ValueError("Production readiness does not match the trusted commercial runtime")

    version = _load_json(client.get(f"{base_url}/api/v1/version"), label="version")
    expected = {
        "schema": "vowpic.runtime-bundle-report.v1",
        "source_sha": expected_source_sha,
        "runtime_bundle_id": expected_runtime_bundle_id,
        "deployment_id": expected_deployment_id,
        "release_role": EXPECTED_RELEASE_ROLE,
        "runtime_environment": "production",
        "schema_revision": EXPECTED_SCHEMA,
    }
    if any(version.get(key) != value for key, value in expected.items()):
        raise ValueError("Production runtime coordinates changed before restoration")
    return version


def _validate_inputs(args: argparse.Namespace) -> str:
    base_url = str(args.base_url or "").strip().rstrip("/")
    if base_url != "https://www.vowpic.com":
        raise ValueError("restoration is restricted to the formal VowPic Production origin")
    if not SHA40.fullmatch(args.expected_source_sha):
        raise ValueError("expected Production source SHA is invalid")
    if not RUNTIME_BUNDLE.fullmatch(args.expected_runtime_bundle_id):
        raise ValueError("expected Production runtime bundle ID is invalid")
    if not DEPLOYMENT_ID.fullmatch(args.expected_deployment_id):
        raise ValueError("expected Production deployment ID is invalid")
    return base_url


def _restore_flags(
    database_url: str,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    deployment_id: str,
    actor: str,
    reason: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-capability-activation",),
            )
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment='production' AND kind='COMMERCIAL_7A'
                  AND (source_sha=%s OR runtime_bundle_id=%s OR api_deployment_id=%s)
                ORDER BY updated_at DESC FOR UPDATE
                """,
                (source_sha, runtime_bundle_id, deployment_id),
            )
            related_commercial = [dict(row) for row in cursor.fetchall()]
            activations = [
                row
                for row in related_commercial
                if row.get("source_sha") == source_sha
                and row.get("runtime_bundle_id") == runtime_bundle_id
                and row.get("api_deployment_id") == deployment_id
                and row.get("phase") not in {"FAILED", "CLEANED"}
            ]
            if len(activations) > 1 or any(row not in activations for row in related_commercial):
                raise ValueError("Production has conflicting COMMERCIAL_7A activation history")
            activation_created = False
            if activations:
                activation = activations[0]
            else:
                cursor.execute(
                    """
                    SELECT * FROM release_activations
                    WHERE environment='production' AND source_sha=%s
                      AND runtime_bundle_id=%s AND api_deployment_id=%s
                      AND manifest_sha256 IS NOT NULL AND api_deployment_url IS NOT NULL
                    ORDER BY updated_at DESC, id DESC
                    FOR UPDATE
                    """,
                    (source_sha, runtime_bundle_id, deployment_id),
                )
                evidence_rows = [dict(row) for row in cursor.fetchall()]
                if not evidence_rows:
                    raise ValueError("the deployed Production runtime has no immutable release evidence")
                evidence = evidence_rows[0]
                evidence_url = urlsplit(str(evidence.get("api_deployment_url") or ""))
                if (
                    evidence.get("api_role") not in {"COMMERCIAL_7A", "COMMERCIAL_7A_API"}
                    or evidence.get("worker_image_digest") is not None
                    or evidence_url.scheme != "https"
                    or not evidence_url.hostname
                    or evidence_url.username
                    or evidence_url.password
                    or evidence_url.query
                    or evidence_url.fragment
                ):
                    raise ValueError("the deployed Production release evidence is incompatible")
                activation_id = str(uuid4())
                approval = f"protected-production:{workflow_run_id}:{workflow_attempt}"
                cursor.execute(
                    """
                    INSERT INTO release_activations (
                      id, environment, kind, source_sha, runtime_bundle_id,
                      manifest_sha256, api_deployment_id, api_deployment_url,
                      api_role, workflow_run_id, workflow_attempt, phase,
                      phase_rank, version, approval
                    ) VALUES (
                      %s, 'production', 'COMMERCIAL_7A', %s, %s, %s, %s, %s,
                      'COMMERCIAL_7A_API', %s, %s, 'ACTIVATED', 10, 1, %s
                    ) RETURNING *
                    """,
                    (
                        activation_id,
                        source_sha,
                        runtime_bundle_id,
                        evidence["manifest_sha256"],
                        deployment_id,
                        evidence["api_deployment_url"],
                        workflow_run_id,
                        workflow_attempt,
                        approval,
                    ),
                )
                activation = dict(cursor.fetchone())
                activation_created = True
            manifest_sha256 = str(activation.get("manifest_sha256") or "")
            if (
                not SHA64.fullmatch(manifest_sha256)
                or activation.get("api_role") not in {"COMMERCIAL_7A", "COMMERCIAL_7A_API"}
                or activation.get("worker_image_digest") is not None
            ):
                raise ValueError("Production COMMERCIAL_7A activation is not compatible")

            cursor.execute(
                """
                SELECT * FROM ops_feature_flags
                WHERE environment='production'
                ORDER BY capability FOR UPDATE
                """
            )
            rows = {str(row["capability"]): dict(row) for row in cursor.fetchall()}
            if set(rows) != set(CAPABILITIES):
                raise ValueError("Production capability inventory is incomplete")

            expected_coordinates = {
                "deployment_id": deployment_id,
                "runtime_bundle_id": runtime_bundle_id,
                "worker_image_digest": None,
                "release_activation_id": activation["id"],
                "target_manifest_sha256": manifest_sha256,
            }
            for capability in CAPABILITIES:
                current = rows[capability]
                state = str(current.get("state") or "")
                if state == "ON":
                    if any(current.get(key) != value for key, value in expected_coordinates.items()):
                        raise ValueError(f"enabled Production coordinate drift: {capability}")
                    events.append({"capability": capability, "old_state": "ON", "new_state": "ON"})
                    continue
                if state != "OFF":
                    raise ValueError(f"Production capability is not at a restorable state: {capability}")

                old_hash = _snapshot_hash(capability, current)
                new_version = int(current["version"]) + 1
                desired = {
                    **expected_coordinates,
                    "state": "ON",
                    "cohort_user_ids": [],
                    "verified_identity_hashes": [],
                    "expires_at": None,
                }
                updated = {**current, **desired, "version": new_version}
                new_hash = _snapshot_hash(capability, updated)
                cursor.execute(
                    """
                    UPDATE ops_feature_flags
                    SET state='ON', deployment_id=%s, runtime_bundle_id=%s,
                        worker_image_digest=NULL, release_activation_id=%s,
                        target_manifest_sha256=%s, cohort_user_ids='[]'::jsonb,
                        verified_identity_hashes='[]'::jsonb, expires_at=NULL,
                        version=%s
                    WHERE id=%s AND version=%s AND state='OFF'
                    RETURNING id
                    """,
                    (
                        deployment_id,
                        runtime_bundle_id,
                        activation["id"],
                        manifest_sha256,
                        new_version,
                        current["id"],
                        current["version"],
                    ),
                )
                if cursor.fetchone() is None:
                    raise ValueError(f"Production capability CAS failed: {capability}")
                cursor.execute(
                    """
                    INSERT INTO ops_feature_flag_audits (
                      id, feature_flag_id, environment, capability, actor, reason,
                      old_state, new_state, old_snapshot_hash, new_snapshot_hash,
                      deployment_id, runtime_bundle_id, target_manifest_sha256, details_json
                    ) VALUES (%s,%s,'production',%s,%s,%s,'OFF','ON',%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(uuid4()),
                        current["id"],
                        capability,
                        actor,
                        reason,
                        old_hash,
                        new_hash,
                        deployment_id,
                        runtime_bundle_id,
                        manifest_sha256,
                        Json({
                            "operation": "restore_full_public_operation",
                            "version": new_version,
                        }),
                    ),
                )
                events.append({"capability": capability, "old_state": "OFF", "new_state": "ON"})

            cursor.execute(
                """
                SELECT capability, state, deployment_id, runtime_bundle_id,
                       release_activation_id, target_manifest_sha256,
                       cohort_user_ids, verified_identity_hashes, expires_at
                FROM ops_feature_flags WHERE environment='production'
                ORDER BY capability
                """
            )
            final_rows = [dict(row) for row in cursor.fetchall()]
            if any(
                row["state"] != "ON"
                or row["deployment_id"] != deployment_id
                or row["runtime_bundle_id"] != runtime_bundle_id
                or row["release_activation_id"] != activation["id"]
                or row["target_manifest_sha256"] != manifest_sha256
                or row["cohort_user_ids"] != []
                or row["verified_identity_hashes"] != []
                or row["expires_at"] is not None
                for row in final_rows
            ):
                raise ValueError("Production capability transaction did not reach the exact full-ON state")

    return {
        "activation_id": str(activation["id"]),
        "activation_phase": activation["phase"],
        "activation_created": activation_created,
        "manifest_sha256": manifest_sha256,
        "events": events,
    }


def _verify_public_operation(
    client: httpx.Client,
    *,
    base_url: str,
    expected_source_sha: str,
    expected_runtime_bundle_id: str,
    expected_deployment_id: str,
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for attempt in range(1, 13):
        _runtime_coordinates(
            client,
            base_url=base_url,
            expected_source_sha=expected_source_sha,
            expected_runtime_bundle_id=expected_runtime_bundle_id,
            expected_deployment_id=expected_deployment_id,
        )
        readiness = _load_json(
            client.get(f"{base_url}/api/v1/ops/readiness"),
            label="commercial readiness",
        )
        public = _load_json(
            client.get(f"{base_url}/api/v1/ops/public_config"),
            label="public configuration",
        )
        capabilities = public.get("capabilities")
        last = {"readiness": readiness, "public_config": public}
        if (
            readiness.get("commercial_ready") is True
            and readiness.get("blockers") == []
            and isinstance(capabilities, dict)
            and set(capabilities) == set(CAPABILITIES)
            and all(capabilities.values())
            and (public.get("auth") or {}).get("google_oauth_enabled") is True
        ):
            login = client.get(f"{base_url}/pages/auth/login")
            if login.status_code != 200:
                raise ValueError("public Google login page is unavailable")
            intent = client.post(
                f"{base_url}/api/v1/auth/oauth-intents",
                headers={
                    "Origin": base_url,
                    "X-Device-Id": "production-full-operation-restore",
                },
                json={"next_path": "/pages/create/index"},
            )
            intent_payload = _load_json(intent, label="Google OAuth intent")
            if not str(intent_payload.get("intent_token") or "").strip():
                raise ValueError("Google OAuth intent was not issued")
            return {
                "attempt": attempt,
                "commercial_ready": True,
                "capabilities": capabilities,
                "google_oauth_intent_issued": True,
                "generation_runtime": (readiness.get("checks") or {}).get("generation_runtime"),
                "storage_config": (readiness.get("checks") or {}).get("storage_config"),
                "payments_config": (readiness.get("checks") or {}).get("payments_config"),
            }
        if attempt < 12:
            time.sleep(5)
    raise ValueError(f"Production did not expose the full capability set: {last!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://www.vowpic.com")
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-runtime-bundle-id", required=True)
    parser.add_argument("--expected-deployment-id", required=True)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        base_url = _validate_inputs(args)
        database_url = os.environ.get(args.database_url_env, "")
        if not database_url:
            raise ValueError("Production migration database URL is missing")
        actor = str(args.actor or "").strip()[:160]
        reason = str(args.reason or "").strip()[:512]
        if len(actor) < 3 or len(reason) < 3:
            raise ValueError("restoration actor and reason are required")
        if not re.fullmatch(r"^[1-9][0-9]{0,19}$", args.workflow_run_id):
            raise ValueError("restoration workflow run ID is invalid")
        if args.workflow_attempt < 1:
            raise ValueError("restoration workflow attempt is invalid")

        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            version = _runtime_coordinates(
                client,
                base_url=base_url,
                expected_source_sha=args.expected_source_sha,
                expected_runtime_bundle_id=args.expected_runtime_bundle_id,
                expected_deployment_id=args.expected_deployment_id,
            )
            database = _restore_flags(
                database_url,
                source_sha=args.expected_source_sha,
                runtime_bundle_id=args.expected_runtime_bundle_id,
                deployment_id=args.expected_deployment_id,
                actor=actor,
                reason=reason,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
            )
            verification = _verify_public_operation(
                client,
                base_url=base_url,
                expected_source_sha=args.expected_source_sha,
                expected_runtime_bundle_id=args.expected_runtime_bundle_id,
                expected_deployment_id=args.expected_deployment_id,
            )

        report = {
            "schema": "vowpic.production-full-operation-restore.v1",
            "passed": True,
            "base_url": base_url,
            "source_sha": version["source_sha"],
            "runtime_bundle_id": version["runtime_bundle_id"],
            "deployment_id": version["deployment_id"],
            "release_role": version["release_role"],
            "schema_revision": version["schema_revision"],
            "database": database,
            "verification": verification,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, httpx.HTTPError, psycopg2.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
