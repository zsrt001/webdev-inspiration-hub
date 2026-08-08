#!/usr/bin/env python3
"""Reserve and clean one exact production GOOGLE_AUTH_ONLY activation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


KIND = "GOOGLE_AUTH_ONLY"
SCHEMA_REVISION = "20260710_0021"
CAPABILITIES = frozenset(
    {
        "google_auth",
        "authenticated_upload",
        "generation",
        "credit_pack_checkout",
        "subscription_billing",
        "private_download",
        "partner_invite",
    }
)
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_BUNDLE_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("GOOGLE_AUTH_ONLY database URL is invalid")
    return clean


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _exact_https_url(value: object) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GOOGLE_AUTH_ONLY base URL must be one exact HTTPS origin")
    return f"https://{parsed.hostname.lower()}"


def validate_runtime_report(
    payload: dict[str, Any], *, source_sha: str, base_url: str
) -> dict[str, str]:
    normalized = {
        "source_sha": str(payload.get("source_sha") or "").strip().lower(),
        "runtime_bundle_id": str(payload.get("runtime_bundle_id") or "").strip().lower(),
        "deployment_id": str(payload.get("deployment_id") or "").strip(),
        "release_role": str(payload.get("release_role") or "").strip(),
        "runtime_environment": str(payload.get("runtime_environment") or "").strip(),
        "schema_revision": str(payload.get("schema_revision") or "").strip(),
        "api_deployment_url": _exact_https_url(base_url),
    }
    if normalized["source_sha"] != source_sha or not SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("GOOGLE_AUTH_ONLY runtime source SHA mismatch")
    if not RUNTIME_BUNDLE_ID.fullmatch(normalized["runtime_bundle_id"]):
        raise ValueError("GOOGLE_AUTH_ONLY runtime bundle ID is invalid")
    if not DEPLOYMENT_ID.fullmatch(normalized["deployment_id"]):
        raise ValueError("GOOGLE_AUTH_ONLY deployment ID is invalid")
    if normalized["release_role"] != "COMMERCIAL_7A":
        raise ValueError("GOOGLE_AUTH_ONLY requires the reviewed production API role")
    if normalized["runtime_environment"] != "production":
        raise ValueError("GOOGLE_AUTH_ONLY requires the production runtime")
    if normalized["schema_revision"] != SCHEMA_REVISION:
        raise ValueError("GOOGLE_AUTH_ONLY runtime schema is not 20260710_0021")
    return normalized


def activation_manifest(coordinates: dict[str, str]) -> dict[str, str]:
    return {
        "schema": "vowpic.google-auth-only-activation.v1",
        "kind": KIND,
        "environment": "production",
        **coordinates,
    }


def _require_all_flags_off(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT capability, state, deployment_id, runtime_bundle_id,
               worker_image_digest, release_activation_id,
               target_manifest_sha256, expires_at
        FROM ops_feature_flags
        WHERE environment = 'production'
        ORDER BY capability
        FOR UPDATE
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != len(CAPABILITIES) or {row["capability"] for row in rows} != CAPABILITIES:
        raise ValueError("production capability inventory is incomplete")
    bound_fields = (
        "deployment_id",
        "runtime_bundle_id",
        "worker_image_digest",
        "release_activation_id",
        "target_manifest_sha256",
        "expires_at",
    )
    if any(
        row["state"] != "OFF" or any(row[field] is not None for field in bound_fields)
        for row in rows
    ):
        raise ValueError("production capabilities are not cleanly OFF")


def reserve_activation(
    database_url: str,
    *,
    coordinates: dict[str, str],
    approval: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    manifest = activation_manifest(coordinates)
    manifest_sha256 = hashlib.sha256(_canonical(manifest)).hexdigest()
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-google-auth-only",),
            )
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions = tuple(str(row["version_num"]) for row in cursor.fetchall())
            if revisions != (SCHEMA_REVISION,):
                raise ValueError("production schema is not exactly 20260710_0021")
            _require_all_flags_off(cursor)
            cursor.execute(
                """
                SELECT id FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND (runtime_bundle_id = %s OR source_sha = %s)
                LIMIT 2
                """,
                (KIND, coordinates["runtime_bundle_id"], coordinates["source_sha"]),
            )
            if cursor.fetchall():
                raise ValueError("GOOGLE_AUTH_ONLY activation already exists for this release")
            activation_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id,
                    manifest_sha256, api_deployment_id, api_deployment_url,
                    api_role, workflow_run_id, workflow_attempt, phase,
                    phase_rank, version, approval
                ) VALUES (
                    %s, 'production', %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'ACCEPTANCE_READY', 1, 1, %s
                )
                RETURNING *
                """,
                (
                    activation_id,
                    KIND,
                    coordinates["source_sha"],
                    coordinates["runtime_bundle_id"],
                    manifest_sha256,
                    coordinates["deployment_id"],
                    coordinates["api_deployment_url"],
                    coordinates["release_role"],
                    workflow_run_id,
                    workflow_attempt,
                    approval,
                ),
            )
            activation = dict(cursor.fetchone())
    return {
        "schema": "vowpic.google-auth-only-activation-report.v1",
        "passed": True,
        "action": "reserve",
        "activation_id": str(activation["id"]),
        "phase": activation["phase"],
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": activation["manifest_sha256"],
    }


def complete_activation(
    database_url: str,
    *,
    source_sha: str,
    deployment_id: str,
    approval: str,
    completion_evidence: bytes,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    report_sha256 = hashlib.sha256(completion_evidence).hexdigest()
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-google-auth-only",),
            )
            _require_all_flags_off(cursor)
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND source_sha = %s AND api_deployment_id = %s
                  AND phase = 'ACCEPTANCE_READY'
                ORDER BY updated_at DESC LIMIT 2 FOR UPDATE
                """,
                (KIND, source_sha, deployment_id),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if len(rows) != 1 or rows[0]["approval"] != approval:
                raise ValueError("exact GOOGLE_AUTH_ONLY activation is not acceptance-ready")
            activation = rows[0]
            cursor.execute(
                """
                SELECT COUNT(*)::integer AS active_unused
                FROM acceptance_identity_bindings
                WHERE environment = 'production' AND deployment_id = %s
                  AND consumed_at IS NULL AND revoked_at IS NULL
            """,
                (deployment_id,),
            )
            if int(cursor.fetchone()["active_unused"]) != 0:
                raise ValueError("unused GOOGLE_AUTH_ONLY identity bindings remain")
            cursor.execute(
                """
                UPDATE release_activations
                SET phase = 'CLEANED', phase_rank = phase_rank + 1,
                    report_sha256 = %s, version = version + 1
                WHERE id = %s AND version = %s AND phase = 'ACCEPTANCE_READY'
                RETURNING *
                """,
                (report_sha256, activation["id"], activation["version"]),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("GOOGLE_AUTH_ONLY activation cleanup lost its CAS fence")
    return {
        "schema": "vowpic.google-auth-only-activation-report.v1",
        "passed": True,
        "action": "complete",
        "activation_id": str(updated["id"]),
        "phase": updated["phase"],
        "source_sha": updated["source_sha"],
        "runtime_bundle_id": updated["runtime_bundle_id"],
        "deployment_id": updated["api_deployment_id"],
        "report_sha256": report_sha256,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reserve", "complete"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--deployment-id")
    parser.add_argument("--base-url")
    parser.add_argument("--runtime-report")
    parser.add_argument("--completion-evidence")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source_sha = str(args.source_sha or "").strip().lower()
        approval = os.environ.get(args.approval_id_env, "").strip()
        if not SOURCE_SHA.fullmatch(source_sha):
            raise ValueError("GOOGLE_AUTH_ONLY source SHA is invalid")
        if not approval or len(approval) > 160:
            raise ValueError("GOOGLE_AUTH_ONLY approval is required")
        database_url = os.environ.get(args.database_url_env, "")
        if args.action == "reserve":
            if not args.runtime_report or not args.base_url:
                raise ValueError("reserve requires a runtime report and base URL")
            payload = json.loads(Path(args.runtime_report).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("GOOGLE_AUTH_ONLY runtime report must be an object")
            coordinates = validate_runtime_report(
                payload, source_sha=source_sha, base_url=args.base_url
            )
            report = reserve_activation(
                database_url,
                coordinates=coordinates,
                approval=approval,
                workflow_run_id=str(os.environ.get("GITHUB_RUN_ID") or "local"),
                workflow_attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT") or "1"),
            )
        else:
            deployment_id = str(args.deployment_id or "").strip()
            if not DEPLOYMENT_ID.fullmatch(deployment_id) or not args.completion_evidence:
                raise ValueError("complete requires exact deployment and completion evidence")
            report = complete_activation(
                database_url,
                source_sha=source_sha,
                deployment_id=deployment_id,
                approval=approval,
                completion_evidence=Path(args.completion_evidence).read_bytes(),
            )
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
