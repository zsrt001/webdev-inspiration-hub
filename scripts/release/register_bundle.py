#!/usr/bin/env python3
"""Store and register one immutable release bundle without mutating deployment env."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest


class PrivateStore(Protocol):
    def put_private(self, object_key: str, data: bytes, content_type: str) -> None: ...
    def read_private(self, object_key: str) -> bytes: ...


_RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COORDINATE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_PREVIEW_RESOLUTION_KEYS = {
    "activation_id", "environment", "kind", "source_sha", "runtime_bundle_id",
    "manifest_sha256", "report_sha256", "api_deployment_id", "api_deployment_url",
    "api_role", "worker_deployment_id", "worker_role", "worker_image_digest",
    "private_evidence_prefix", "workflow_run_id", "workflow_attempt", "phase",
}


def _aware_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reservation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _validate_preview_resolution(
    payload: dict[str, Any], *, source_sha: str
) -> tuple[dict[str, Any], str]:
    if set(payload) != _PREVIEW_RESOLUTION_KEYS:
        raise ValueError("Preview resolution fields do not match the resolver contract")
    if payload.get("environment") != "preview" or payload.get("kind") != "PREVIEW_COMMERCIAL":
        raise ValueError("Production reservation requires PREVIEW_COMMERCIAL authority")
    if payload.get("phase") != "CLEANED":
        raise ValueError("Production reservation requires a CLEANED Preview activation")
    if payload.get("source_sha") != source_sha:
        raise ValueError("Preview resolution source SHA mismatch")
    try:
        UUID(str(payload.get("activation_id")))
    except (TypeError, ValueError) as exc:
        raise ValueError("Preview activation ID is invalid") from exc
    checks = (
        (_RUNTIME_ID, payload.get("runtime_bundle_id"), "runtime bundle ID"),
        (_SHA64, payload.get("manifest_sha256"), "manifest hash"),
        (_SHA64, payload.get("report_sha256"), "report hash"),
        (_IMAGE_DIGEST, payload.get("worker_image_digest"), "Worker image digest"),
        (_COORDINATE, payload.get("api_deployment_id"), "API deployment ID"),
        (_COORDINATE, payload.get("worker_deployment_id"), "Worker deployment ID"),
    )
    for pattern, value, label in checks:
        if not pattern.fullmatch(str(value or "")):
            raise ValueError(f"Preview {label} is invalid")
    if payload.get("api_role") != "PREVIEW_COMMERCIAL_API":
        raise ValueError("Preview API role mismatch")
    if payload.get("worker_role") != "PREVIEW_COMMERCIAL_WORKER":
        raise ValueError("Preview Worker role mismatch")
    _exact_https_url(str(payload.get("api_deployment_url") or ""))
    prefix = str(payload.get("private_evidence_prefix") or "").strip().strip("/\\")
    if not prefix or ".." in Path(prefix).parts or "latest" in {part.lower() for part in Path(prefix).parts}:
        raise ValueError("Preview evidence prefix is mutable or invalid")
    if not _RUN_ID.fullmatch(str(payload.get("workflow_run_id") or "")):
        raise ValueError("Preview workflow run ID is invalid")
    attempt = payload.get("workflow_attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("Preview workflow attempt is invalid")
    normalized = dict(payload)
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized, digest


def build_production_reservation(
    *,
    kind: str,
    environment: str,
    source_sha: str,
    preview_resolution: dict[str, Any],
    workflow_run_id: str,
    workflow_attempt: int,
    approval: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    if kind != "COMMERCIAL_7A" or environment != "production":
        raise ValueError("only the COMMERCIAL_7A Production reservation is implemented")
    if not _SOURCE_SHA.fullmatch(str(source_sha or "")):
        raise ValueError("Production source SHA is invalid")
    _, preview_hash = _validate_preview_resolution(preview_resolution, source_sha=source_sha)
    clean_run = str(workflow_run_id or "").strip()
    if not _RUN_ID.fullmatch(clean_run):
        raise ValueError("Production workflow run ID is invalid")
    if not isinstance(workflow_attempt, int) or isinstance(workflow_attempt, bool) or workflow_attempt < 1:
        raise ValueError("Production workflow attempt is invalid")
    clean_approval = str(approval or "").strip()
    if not clean_approval or len(clean_approval) > 160:
        raise ValueError("Production approval reference is required")
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ({
        "id": str(uuid4()),
        "environment": "production",
        "kind": "COMMERCIAL_7A",
        "source_sha": source_sha,
        "runtime_bundle_id": None,
        "workflow_run_id": clean_run,
        "workflow_attempt": workflow_attempt,
        "phase": "RESERVED",
        "phase_rank": 0,
        "version": 1,
        "approval": clean_approval,
        "reservation_expires_at": created + timedelta(hours=2),
    }, preview_hash)


def decide_production_reservation(
    *,
    active_rows: list[dict[str, Any]],
    same_attempt_rows: list[dict[str, Any]],
    requested: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if len(active_rows) > 1 or len(same_attempt_rows) > 1:
        raise ValueError("ambiguous Production reservation state")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if same_attempt_rows:
        row = same_attempt_rows[0]
        if not active_rows or str(active_rows[0].get("id")) != str(row.get("id")):
            raise ValueError("a terminal Production workflow attempt cannot be reused")
        comparable = (
            "environment", "kind", "source_sha", "workflow_run_id", "workflow_attempt",
            "phase", "phase_rank", "approval",
        )
        if any(str(row.get(key)) != str(requested.get(key)) for key in comparable):
            raise ValueError("Production workflow attempt is bound to different coordinates")
        if _aware_timestamp(row.get("reservation_expires_at")) <= current:
            raise ValueError("Production reservation has expired")
        return row
    if active_rows:
        raise ValueError("another active Production release must be reconciled first")
    return None


def _existing_bytes(store: PrivateStore, key: str) -> bytes | None:
    try:
        return store.read_private(key)
    except FileNotFoundError:
        return None


def store_manifest_create_once(
    store: PrivateStore,
    *,
    manifest_path: Path,
    run_id: str,
    attempt: int,
) -> dict[str, str]:
    clean_run = str(run_id or "").strip()
    if not _RUN_ID.fullmatch(clean_run) or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("workflow run/attempt coordinate is invalid")
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest file is invalid JSON") from exc
    normalized = validate_manifest(manifest)
    if canonical_manifest_bytes(normalized) != raw:
        raise ValueError("manifest file is not canonical")
    digest = hashlib.sha256(raw).hexdigest()
    prefix = (
        f"artifacts/release/{normalized['source_sha']}/{clean_run}-{attempt}/"
        f"{normalized['api_deployment_id']}/{digest}"
    )
    object_key = f"{prefix}/00-bundle-manifest.json"
    existing = _existing_bytes(store, object_key)
    if existing is not None:
        if existing != raw:
            raise ValueError("content-addressed manifest object is corrupted")
        return {
            "state": "ALREADY_STORED",
            "manifest_sha256": digest,
            "object_key": object_key,
            "evidence_prefix": prefix,
        }
    try:
        store.put_private(object_key, raw, "application/json")
    except (FileExistsError, RuntimeError):
        existing = _existing_bytes(store, object_key)
        if existing != raw:
            raise
        return {
            "state": "ALREADY_STORED",
            "manifest_sha256": digest,
            "object_key": object_key,
            "evidence_prefix": prefix,
        }
    if store.read_private(object_key) != raw:
        raise ValueError("private manifest read-back hash mismatch")
    return {
        "state": "STORED",
        "manifest_sha256": digest,
        "object_key": object_key,
        "evidence_prefix": prefix,
    }


def _exact_https_url(value: str) -> str:
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
        raise ValueError("API deployment URL must be an exact HTTPS origin")
    return f"https://{parsed.netloc}"


def build_registration_record(
    manifest: dict[str, Any],
    *,
    stored: dict[str, str],
    api_deployment_url: str,
    workflow_run_id: str,
    workflow_attempt: int,
    approval: str,
    report_sha256: str,
) -> dict[str, Any]:
    normalized = validate_manifest(manifest)
    manifest_sha = str(stored.get("manifest_sha256") or "").strip().lower()
    report_sha = str(report_sha256 or "").strip().lower()
    evidence_prefix = str(stored.get("evidence_prefix") or "").strip().strip("/\\")
    if not _SHA64.fullmatch(manifest_sha) or not _SHA64.fullmatch(report_sha):
        raise ValueError("registration manifest/report hash is invalid")
    if "latest" in {part.lower() for part in Path(evidence_prefix).parts} or not evidence_prefix:
        raise ValueError("registration evidence prefix is mutable or empty")
    clean_run = str(workflow_run_id or "").strip()
    if not _RUN_ID.fullmatch(clean_run) or not isinstance(workflow_attempt, int) or workflow_attempt < 1:
        raise ValueError("registration workflow coordinate is invalid")
    clean_approval = str(approval or "").strip()
    if not clean_approval or len(clean_approval) > 160:
        raise ValueError("registration approval reference is required")
    role = normalized["release_role"]
    environment = "preview" if role.startswith("PREVIEW_") else "production"
    kind = "SAFE_BASELINE_INSTALL" if role == "SAFE_BASELINE" else role
    worker_role = f"{role}_WORKER" if normalized["worker_deployment_id"] else None
    return {
        "id": str(uuid4()),
        "environment": environment,
        "kind": kind,
        "source_sha": normalized["source_sha"],
        "runtime_bundle_id": normalized["runtime_bundle_id"],
        "manifest_sha256": manifest_sha,
        "report_sha256": report_sha,
        "api_deployment_id": normalized["api_deployment_id"],
        "api_deployment_url": _exact_https_url(api_deployment_url),
        "api_role": f"{role}_API",
        "worker_deployment_id": normalized["worker_deployment_id"],
        "worker_role": worker_role,
        "worker_image_digest": normalized["worker_image_digest"],
        "private_evidence_prefix": evidence_prefix,
        "workflow_run_id": clean_run,
        "workflow_attempt": workflow_attempt,
        "phase": "COMPLETED",
        "phase_rank": 2,
        "version": 1,
        "approval": clean_approval,
    }


def _database_url(value: str) -> str:
    url = str(value or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://"):]
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("control-plane database URL is invalid")
    return url


def register_activation_cas(database_url: str, record: dict[str, Any]) -> dict[str, str]:
    """Serialize and create/reuse only one exact service-owned activation row."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    comparable = tuple(
        field
        for field in record
        if field not in {"id"}
    )
    lock_key = f"{record['environment']}:{record['kind']}:{record['source_sha']}"
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = %s AND kind = %s
                  AND (source_sha = %s OR runtime_bundle_id = %s)
                ORDER BY created_at DESC
                FOR UPDATE
                """,
                (
                    record["environment"], record["kind"], record["source_sha"],
                    record["runtime_bundle_id"],
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if rows:
                if len(rows) != 1 or any(str(rows[0].get(field)) != str(record[field]) for field in comparable):
                    raise ValueError("release activation conflicts with existing service-owned coordinates")
                return {"state": "ALREADY_REGISTERED", "activation_id": str(rows[0]["id"])}
            columns = tuple(record)
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO release_activations ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(record[column] for column in columns),
            )
    return {"state": "REGISTERED", "activation_id": str(record["id"])}


def _preview_row_matches(row: dict[str, Any], preview: dict[str, Any]) -> bool:
    compared = _PREVIEW_RESOLUTION_KEYS - {"activation_id"}
    return (
        str(row.get("id")) == str(preview["activation_id"])
        and all(str(row.get(key)) == str(preview[key]) for key in compared)
    )


def reserve_production_activation_cas(
    database_url: str,
    *,
    record: dict[str, Any],
    preview_resolution: dict[str, Any],
    preview_resolution_sha256: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Reserve or rediscover one exact release after rechecking Preview authority."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    preview, actual_preview_hash = _validate_preview_resolution(
        preview_resolution, source_sha=record["source_sha"]
    )
    if actual_preview_hash != preview_resolution_sha256:
        raise ValueError("Preview resolution hash drifted before reservation")
    lock_key = f"{record['environment']}:{record['kind']}"
    terminal_phases = (
        "COMPLETED", "CLEANED", "PASSED", "FAILED", "DISARMED", "PRODUCTION_ACCEPTED"
    )
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
            cursor.execute(
                """
                SELECT id, environment, kind, source_sha, runtime_bundle_id, manifest_sha256,
                       report_sha256, api_deployment_id, api_deployment_url, api_role,
                       worker_deployment_id, worker_role, worker_image_digest,
                       private_evidence_prefix, workflow_run_id, workflow_attempt, phase
                FROM release_activations
                WHERE id = %s
                FOR SHARE
                """,
                (preview["activation_id"],),
            )
            preview_rows = [dict(row) for row in cursor.fetchall()]
            if len(preview_rows) != 1 or not _preview_row_matches(preview_rows[0], preview):
                raise ValueError("Preview resolution no longer matches service-owned authority")
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = %s AND kind = %s
                  AND phase NOT IN %s
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (record["environment"], record["kind"], terminal_phases),
            )
            active_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = %s AND kind = %s
                  AND workflow_run_id = %s AND workflow_attempt = %s
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (
                    record["environment"], record["kind"], record["workflow_run_id"],
                    record["workflow_attempt"],
                ),
            )
            same_attempt_rows = [dict(row) for row in cursor.fetchall()]
            existing = decide_production_reservation(
                active_rows=active_rows,
                same_attempt_rows=same_attempt_rows,
                requested=record,
                now=now,
            )
            if existing is not None:
                return {
                    "state": "ALREADY_RESERVED",
                    "activation_id": str(existing["id"]),
                    "preview_activation_id": str(preview["activation_id"]),
                    "preview_resolution_sha256": actual_preview_hash,
                    "reservation_expires_at": _aware_timestamp(
                        existing["reservation_expires_at"]
                    ).isoformat(),
                }
            columns = tuple(record)
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO release_activations ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(record[column] for column in columns),
            )
    return {
        "state": "RESERVED",
        "activation_id": str(record["id"]),
        "preview_activation_id": str(preview["activation_id"]),
        "preview_resolution_sha256": actual_preview_hash,
        "reservation_expires_at": _aware_timestamp(record["reservation_expires_at"]).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _reserve_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Reserve one Production release lease")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--environment", required=True, choices=("production",))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--preview-resolution-report", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        preview_path = Path(args.preview_resolution_report)
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        if not isinstance(preview, dict):
            raise ValueError("Preview resolution report must be a JSON object")
        now = datetime.now(timezone.utc)
        record, preview_hash = build_production_reservation(
            kind=args.kind,
            environment=args.environment,
            source_sha=args.source_sha,
            preview_resolution=preview,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            approval=os.environ.get(args.approval_id_env, ""),
            now=now,
        )
        result = reserve_production_activation_cas(
            os.environ.get(args.database_url_env, ""),
            record=record,
            preview_resolution=preview,
            preview_resolution_sha256=preview_hash,
            now=now,
        )
        _write_create_once(Path(args.output), result)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _register_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--api-deployment-url", required=True)
    parser.add_argument("--report-sha256", required=True)
    parser.add_argument("--database-url-env", default="CONTROL_PLANE_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="RELEASE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        from app.services.storage import StorageService

        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stored = store_manifest_create_once(
            StorageService(),
            manifest_path=manifest_path,
            run_id=args.workflow_run_id,
            attempt=args.workflow_attempt,
        )
        record = build_registration_record(
            manifest,
            stored=stored,
            api_deployment_url=args.api_deployment_url,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            approval=os.environ.get(args.approval_id_env, ""),
            report_sha256=args.report_sha256,
        )
        registration = register_activation_cas(
            os.environ.get(args.database_url_env, ""), record
        )
        result = {**stored, **registration}
        _write_create_once(Path(args.output), result)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["reserve"]:
        return _reserve_main(argv[1:])
    return _register_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
