#!/usr/bin/env python3
"""CAS-register one exact Preview identity or website-backend runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.github_artifact_evidence import parse_reference  # noqa: E402


ROLE = "PREVIEW_IDENTITY"
ALLOWED_ROLES = frozenset({"PREVIEW_IDENTITY", "PREVIEW_COMMERCIAL"})
ENVIRONMENT = "preview"
SCHEMA_REVISION = "20260710_0020"
RESERVATION_TTL = timedelta(hours=2)


def validate_coordinates(
    *,
    release_role: object = ROLE,
    source_sha: object,
    runtime_bundle_id: object,
    worker_image_digest: object = None,
    workflow_run_id: object,
    workflow_attempt: object,
) -> dict[str, Any]:
    role = str(release_role or "").strip().upper()
    source = str(source_sha or "").strip().lower()
    runtime = str(runtime_bundle_id or "").strip().lower()
    run_id = str(workflow_run_id or "").strip()
    try:
        attempt = int(workflow_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow attempt must be a positive integer") from exc
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source):
        raise ValueError("source SHA must be an exact lowercase Git digest")
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime):
        raise ValueError("runtime bundle must be a canonical rtb_ SHA-256 identity")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise ValueError("workflow run ID must be a positive decimal ID")
    if attempt <= 0:
        raise ValueError("workflow attempt must be positive")
    if role not in ALLOWED_ROLES:
        raise ValueError("Preview release role is not allowlisted")
    if str(worker_image_digest or "").strip():
        raise ValueError("Preview activations are website-backend-only")
    coordinates = {
        "environment": ENVIRONMENT,
        "kind": role,
        "source_sha": source,
        "runtime_bundle_id": runtime,
        "api_role": role if role == "PREVIEW_IDENTITY" else "PREVIEW_COMMERCIAL_API",
        "workflow_run_id": run_id,
        "workflow_attempt": attempt,
        "schema_revision": SCHEMA_REVISION,
    }
    return coordinates


def _deployment_url(value: object) -> str:
    clean = str(value or "").strip().rstrip("/")
    if "://" not in clean:
        clean = f"https://{clean}"
    parsed = urlsplit(clean)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.lower().endswith(".vercel.app")
    ):
        raise ValueError("deployment URL must be one exact HTTPS Vercel origin")
    return f"https://{parsed.hostname.lower()}"


def _sha256(value: object, *, label: str) -> str:
    clean = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", clean):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return clean


def build_activation_report(
    *,
    activation_id: str,
    coordinates: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    UUID(activation_id)
    validated = validate_coordinates(
        release_role=coordinates.get("kind"),
        source_sha=coordinates.get("source_sha"),
        runtime_bundle_id=coordinates.get("runtime_bundle_id"),
        workflow_run_id=coordinates.get("workflow_run_id"),
        workflow_attempt=coordinates.get("workflow_attempt"),
    )
    deployment_id = str(coordinates.get("api_deployment_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", deployment_id):
        raise ValueError("Vercel deployment ID is invalid")
    deployment_url = _deployment_url(coordinates.get("api_deployment_url"))
    manifest = _sha256(coordinates.get("manifest_sha256"), label="manifest SHA-256")
    if any(
        coordinates.get(field) is not None
        for field in ("worker_deployment_id", "worker_role", "worker_image_digest")
    ):
        raise ValueError("Preview activation must not bind external Worker coordinates")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("activation report timestamp must be timezone-aware")
    report = {
        "report_version": (
            "vowpic.preview-commercial-activation.v1"
            if validated["kind"] == "PREVIEW_COMMERCIAL"
            else "vowpic.preview-identity-activation.v3"
        ),
        "passed": True,
        "activation_id": activation_id,
        **validated,
        "api_deployment_id": deployment_id,
        "api_deployment_url": deployment_url,
        "manifest_sha256": manifest,
        "phase": "COMPLETED",
        "created_at": current.astimezone(timezone.utc).isoformat(),
    }
    return report


def write_create_once_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _database_url(value: str) -> str:
    clean = value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    if not clean:
        raise ValueError("Preview control-plane database URL is required")
    return clean


def _approval(value: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > 160:
        raise ValueError("protected Preview approval ID is required")
    return clean


def _activation_matches(row: dict[str, Any], coordinates: dict[str, Any]) -> bool:
    keys = [
        "environment", "kind", "source_sha", "runtime_bundle_id", "api_role",
        "workflow_run_id", "workflow_attempt",
    ]
    return all(str(row.get(key)) == str(coordinates[key]) for key in keys) and all(
        row.get(field) is None
        for field in ("worker_role", "worker_image_digest", "worker_deployment_id")
    )


def decide_reservation(
    active_rows: list[dict[str, Any]],
    same_attempt_rows: list[dict[str, Any]],
    same_runtime_rows: list[dict[str, Any]],
    coordinates: dict[str, Any],
) -> dict[str, Any] | None:
    if len(active_rows) > 1:
        raise ValueError("ambiguous active Preview activation reservation")
    if active_rows:
        row = active_rows[0]
        if not _activation_matches(row, coordinates):
            raise ValueError("another Preview identity activation must be cleaned first")
        return {
            "state": f"ALREADY_{row['phase']}",
            "activation_id": str(row["id"]),
            "version": int(row["version"]),
        }
    if len(same_attempt_rows) > 1:
        raise ValueError("ambiguous Preview workflow-attempt reservation")
    if same_attempt_rows:
        row = same_attempt_rows[0]
        if not _activation_matches(row, coordinates):
            raise ValueError("Preview workflow attempt is bound to different coordinates")
        if row["phase"] == "CLEANED":
            raise ValueError("a CLEANED Preview activation cannot be reopened")
        raise ValueError("inactive Preview activation has an invalid phase")
    if len(same_runtime_rows) > 1:
        raise ValueError("ambiguous Preview runtime bundle reservation")
    if same_runtime_rows:
        row = same_runtime_rows[0]
        if _activation_matches(row, coordinates):
            raise ValueError("a CLEANED Preview activation cannot be reopened")
        raise ValueError("a Preview runtime bundle cannot be reused by another workflow attempt")
    return None


def reserve_activation(
    database_url: str,
    *,
    coordinates: dict[str, Any],
    approval: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("reservation timestamp must be timezone-aware")
    activation_id = str(uuid4())
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"vowpic.{coordinates['kind'].lower()}.activation",),
            )
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'preview' AND kind = %s
                  AND phase <> 'CLEANED'
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (coordinates["kind"],),
            )
            active_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'preview' AND kind = %s
                  AND workflow_run_id = %s AND workflow_attempt = %s
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (coordinates["kind"], coordinates["workflow_run_id"], coordinates["workflow_attempt"]),
            )
            same_attempt_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'preview' AND kind = %s
                  AND runtime_bundle_id = %s
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (coordinates["kind"], coordinates["runtime_bundle_id"]),
            )
            same_runtime_rows = [dict(row) for row in cursor.fetchall()]
            decision = decide_reservation(
                active_rows,
                same_attempt_rows,
                same_runtime_rows,
                coordinates,
            )
            if decision is not None:
                return decision
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id, api_role,
                    worker_role, worker_image_digest,
                    workflow_run_id, workflow_attempt, phase, phase_rank, version,
                    approval, reservation_expires_at
                ) VALUES (
                    %s, 'preview', %s, %s, %s, %s, %s, %s,
                    %s, %s, 'RESERVED', 0, 1, %s, %s
                )
                """,
                (
                    activation_id,
                    coordinates["kind"],
                    coordinates["source_sha"],
                    coordinates["runtime_bundle_id"],
                    coordinates["api_role"],
                    coordinates.get("worker_role"),
                    coordinates.get("worker_image_digest"),
                    coordinates["workflow_run_id"],
                    coordinates["workflow_attempt"],
                    _approval(approval),
                    current + RESERVATION_TTL,
                ),
            )
    return {"state": "RESERVED", "activation_id": activation_id, "version": 1}


def resolve_vercel_deployment(
    deployment_url: str,
    *,
    coordinates: dict[str, Any],
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> dict[str, str]:
    origin = _deployment_url(deployment_url)
    clean_token = token.strip()
    clean_project = project_id.strip()
    clean_team = team_id.strip()
    if not clean_token or not clean_project or not clean_team:
        raise ValueError("Vercel token, project ID, and team ID are required")
    hostname = urlsplit(origin).hostname or ""
    response = client.get(
        f"https://api.vercel.com/v13/deployments/{quote(hostname, safe='')}",
        params={"teamId": clean_team},
        headers={"Authorization": f"Bearer {clean_token}"},
    )
    if response.status_code != 200:
        raise ValueError(f"Vercel deployment lookup failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Vercel deployment lookup returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Vercel deployment lookup returned an invalid object")
    response_project = str(
        payload.get("projectId")
        or ((payload.get("project") or {}).get("id") if isinstance(payload.get("project"), dict) else "")
        or ""
    ).strip()
    if response_project != clean_project:
        raise ValueError("Vercel deployment is outside the protected project")
    reported_url = _deployment_url(payload.get("url"))
    if reported_url != origin:
        raise ValueError("Vercel deployment URL read-back mismatch")
    if str(payload.get("readyState") or payload.get("state") or "").upper() != "READY":
        raise ValueError("Vercel deployment is not READY")
    metadata = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    expected_metadata = {
        "vowpicSourceSha": coordinates["source_sha"],
        "vowpicRuntimeBundleId": coordinates["runtime_bundle_id"],
        "vowpicReleaseRole": coordinates["kind"],
    }
    if any(str(metadata.get(key) or "") != value for key, value in expected_metadata.items()):
        raise ValueError("Vercel deployment metadata does not match the protected runtime")
    deployment_id = str(payload.get("uid") or payload.get("id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", deployment_id):
        raise ValueError("Vercel deployment ID is missing or invalid")
    return {"api_deployment_id": deployment_id, "api_deployment_url": origin}


def verify_runtime_version(
    deployment: dict[str, str],
    *,
    coordinates: dict[str, Any],
    bypass_secret: str,
    client: httpx.Client,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bypass_secret.strip():
        headers["x-vercel-protection-bypass"] = bypass_secret.strip()
    response = client.get(f"{deployment['api_deployment_url']}/version", headers=headers)
    if response.status_code != 200:
        raise ValueError(f"Preview runtime /version failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Preview runtime /version returned invalid JSON") from exc
    expected = {
        "schema": "vowpic.runtime-bundle-report.v1",
        "source_sha": coordinates["source_sha"],
        "runtime_bundle_id": coordinates["runtime_bundle_id"],
        "deployment_id": deployment["api_deployment_id"],
        "release_role": coordinates["kind"],
        "runtime_environment": ENVIRONMENT,
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Preview runtime /version coordinates mismatch")
    return payload


def mark_deployed(
    database_url: str,
    *,
    activation_id: str,
    coordinates: dict[str, Any],
    deployment: dict[str, str],
    manifest_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    UUID(activation_id)
    manifest = _sha256(manifest_sha256, label="manifest SHA-256")
    current = now or datetime.now(timezone.utc)
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM release_activations WHERE id = %s FOR UPDATE", (activation_id,))
            raw = cursor.fetchone()
            if raw is None:
                raise ValueError("Preview activation does not exist")
            row = dict(raw)
            if not _activation_matches(row, coordinates):
                raise ValueError("Preview activation coordinates mismatch")
            expected_deployment = (
                deployment["api_deployment_id"], deployment["api_deployment_url"],
                None, manifest,
            )
            recorded = (
                row.get("api_deployment_id"), row.get("api_deployment_url"),
                row.get("worker_deployment_id"), row.get("manifest_sha256"),
            )
            if row["phase"] in {"DEPLOYED", "COMPLETED"} and recorded == expected_deployment:
                return {"state": f"ALREADY_{row['phase']}", "activation_id": activation_id}
            if row["phase"] != "RESERVED":
                raise ValueError("Preview activation is not in RESERVED")
            if row["reservation_expires_at"] <= current:
                raise ValueError("Preview activation reservation expired")
            cursor.execute(
                """
                UPDATE release_activations
                SET api_deployment_id = %s, api_deployment_url = %s,
                    worker_deployment_id = %s, manifest_sha256 = %s,
                    phase = 'DEPLOYED', phase_rank = 1,
                    version = version + 1
                WHERE id = %s AND version = %s AND phase = 'RESERVED'
                """,
                (*expected_deployment, activation_id, row["version"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Preview DEPLOYED phase CAS failed")
    return {"state": "DEPLOYED", "activation_id": activation_id}


def complete_activation(
    database_url: str,
    *,
    activation_id: str,
    coordinates: dict[str, Any],
    report_path: Path,
    evidence_reference: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    raw_report = report_path.read_bytes()
    try:
        report = json.loads(raw_report.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Preview activation report is invalid JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("Preview activation report must be an object")
    reference = parse_reference(evidence_reference)
    if (
        reference["run_id"] != coordinates["workflow_run_id"]
        or reference["report_name"] != report_path.name
    ):
        raise ValueError("Preview activation evidence reference mismatch")
    report_hash = hashlib.sha256(raw_report).hexdigest()
    if report.get("activation_id") != activation_id or report.get("passed") is not True:
        raise ValueError("Preview activation report does not pass for this activation")
    for key in (
        "environment", "kind", "source_sha", "runtime_bundle_id", "api_role",
        "workflow_run_id", "workflow_attempt", "schema_revision",
    ):
        if str(report.get(key)) != str(coordinates[key]):
            raise ValueError(f"Preview activation report {key} mismatch")
    if any(
        report.get(field) is not None
        for field in ("worker_role", "worker_image_digest", "worker_deployment_id")
    ):
        raise ValueError("Preview activation report contains external Worker coordinates")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM release_activations WHERE id = %s FOR UPDATE", (activation_id,))
            raw = cursor.fetchone()
            if raw is None:
                raise ValueError("Preview activation does not exist")
            row = dict(raw)
            if not _activation_matches(row, coordinates):
                raise ValueError("Preview activation coordinates mismatch")
            deployment_keys = ["api_deployment_id", "api_deployment_url", "manifest_sha256"]
            for key in deployment_keys:
                if report.get(key) != row.get(key):
                    raise ValueError(f"Preview activation report {key} mismatch")
            if row["phase"] == "COMPLETED":
                if row["report_sha256"] != report_hash or row["private_evidence_prefix"] != evidence_reference:
                    raise ValueError("completed Preview activation evidence is immutable")
                return {"state": "ALREADY_COMPLETED", "activation_id": activation_id}
            if row["phase"] != "DEPLOYED":
                raise ValueError("Preview activation is not in DEPLOYED")
            cursor.execute(
                """
                UPDATE release_activations
                SET report_sha256 = %s, private_evidence_prefix = %s,
                    phase = 'COMPLETED', phase_rank = 2, version = version + 1
                WHERE id = %s AND version = %s AND phase = 'DEPLOYED'
                """,
                (report_hash, evidence_reference, activation_id, row["version"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Preview COMPLETED phase CAS failed")
    return {"state": "COMPLETED", "activation_id": activation_id, "report_sha256": report_hash}


def _runtime_id(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", value):
        raise ValueError("runtime ID file is invalid")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("reserve", "deployed", "complete"):
        command = subparsers.add_parser(name)
        command.add_argument("--role", choices=sorted(ALLOWED_ROLES), default=ROLE)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--runtime-id-file", required=True)
        command.add_argument("--workflow-run-id", required=True)
        command.add_argument("--workflow-attempt", required=True, type=int)
        command.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
        command.add_argument("--activation-id-file", required=True)
        command.add_argument("--output", required=True)
    reserve = subparsers.choices["reserve"]
    reserve.add_argument("--approval-id-env", default="")
    deployed = subparsers.choices["deployed"]
    deployed.add_argument("--deployment-url", required=True)
    deployed.add_argument("--manifest-sha256", required=True)
    deployed.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
    deployed.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
    deployed.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
    deployed.add_argument("--bypass-secret-env", default="VERCEL_AUTOMATION_BYPASS_SECRET")
    deployed.add_argument("--activation-report", required=True)
    complete = subparsers.choices["complete"]
    complete.add_argument("--activation-report", required=True)
    complete.add_argument("--evidence-reference-file", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "").strip()
        runtime_id = _runtime_id(Path(args.runtime_id_file))
        coordinates = validate_coordinates(
            release_role=args.role,
            source_sha=args.source_sha,
            runtime_bundle_id=runtime_id,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
        )
        activation_file = Path(args.activation_id_file)
        if args.action == "reserve":
            approval_env = args.approval_id_env or (
                "PREVIEW_COMMERCIAL_APPROVAL_ID"
                if args.role == "PREVIEW_COMMERCIAL"
                else "PREVIEW_IDENTITY_APPROVAL_ID"
            )
            result = reserve_activation(
                database_url,
                coordinates=coordinates,
                approval=os.environ.get(approval_env, ""),
            )
            activation_file.parent.mkdir(parents=True, exist_ok=True)
            with activation_file.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{result['activation_id']}\n")
        else:
            activation_id = activation_file.read_text(encoding="utf-8").strip()
            UUID(activation_id)
            if args.action == "deployed":
                with httpx.Client(timeout=30.0) as client:
                    deployment = resolve_vercel_deployment(
                        args.deployment_url,
                        coordinates=coordinates,
                        token=os.environ.get(args.vercel_token_env, ""),
                        project_id=os.environ.get(args.vercel_project_id_env, ""),
                        team_id=os.environ.get(args.vercel_team_id_env, ""),
                        client=client,
                    )
                    verify_runtime_version(
                        deployment,
                        coordinates=coordinates,
                        bypass_secret=os.environ.get(args.bypass_secret_env, ""),
                        client=client,
                    )
                result = mark_deployed(
                    database_url,
                    activation_id=activation_id,
                    coordinates=coordinates,
                    deployment=deployment,
                    manifest_sha256=args.manifest_sha256,
                )
                report = build_activation_report(
                    activation_id=activation_id,
                    coordinates={
                        **coordinates,
                        **deployment,
                        "manifest_sha256": args.manifest_sha256,
                    },
                )
                write_create_once_json(Path(args.activation_report), report)
            else:
                evidence_reference = Path(args.evidence_reference_file).read_text(
                    encoding="utf-8"
                ).strip()
                result = complete_activation(
                    database_url,
                    activation_id=activation_id,
                    coordinates=coordinates,
                    report_path=Path(args.activation_report),
                    evidence_reference=evidence_reference,
                )
        write_create_once_json(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
