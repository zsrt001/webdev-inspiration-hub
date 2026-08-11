#!/usr/bin/env python3
"""Reserve and clean one exact production GOOGLE_AUTH_ONLY activation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
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
REPORT_SHA256 = re.compile(r"^[0-9a-f]{64}$")
PRODUCTION_ACTIVATION_FENCE = "vowpic-production-capability-activation"
RUNTIME_BUNDLE_INDEX = "uq_release_activation_runtime_bundle"
LEGACY_RUNTIME_BUNDLE_PREDICATE = "runtime_bundle_idisnotnull"
RETRYABLE_RUNTIME_BUNDLE_PREDICATE = (
    "runtime_bundle_idisnotnullandnotkind='google_auth_only'andphase='cleaned'"
)


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


def _require_retryable_release_history(
    rows: list[dict[str, Any]], *, deployment_id: str
) -> None:
    for row in rows:
        report_sha256 = str(row.get("report_sha256") or "").strip().lower()
        if (
            row.get("phase") != "CLEANED"
            or not REPORT_SHA256.fullmatch(report_sha256)
            or row.get("api_deployment_id") == deployment_id
        ):
            raise ValueError(
                "GOOGLE_AUTH_ONLY release history is not cleanly retryable"
            )


def _normalized_index_predicate(value: object) -> str:
    clean = str(value or "").strip().lower()
    clean = re.sub(r"::[a-z_][a-z0-9_]*(?:\[\])?", "", clean)
    return re.sub(r"[\s()]+", "", clean)


def _runtime_bundle_index(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT index.indisunique, index.indisvalid, index.indisready,
               ARRAY(
                 SELECT attribute.attname
                 FROM unnest(index.indkey) WITH ORDINALITY AS key(attnum, position)
                 JOIN pg_attribute AS attribute
                   ON attribute.attrelid = index.indrelid
                  AND attribute.attnum = key.attnum
                 WHERE key.position <= index.indnkeyatts
                 ORDER BY key.position
               ) AS key_columns,
               pg_get_expr(index.indpred, index.indrelid) AS predicate
        FROM pg_index AS index
        JOIN pg_class AS relation ON relation.oid = index.indrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_class AS index_relation ON index_relation.oid = index.indexrelid
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'release_activations'
          AND index_relation.relname = %s
        """,
        (RUNTIME_BUNDLE_INDEX,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("GOOGLE_AUTH_ONLY runtime-bundle index is missing")
    result = dict(row)
    if (
        result.get("indisunique") is not True
        or result.get("indisvalid") is not True
        or result.get("indisready") is not True
        or list(result.get("key_columns") or [])
        != ["environment", "kind", "runtime_bundle_id"]
    ):
        raise ValueError("GOOGLE_AUTH_ONLY runtime-bundle index is invalid")
    return result


def _ensure_retryable_runtime_bundle_index(cursor: Any) -> None:
    index = _runtime_bundle_index(cursor)
    predicate = _normalized_index_predicate(index.get("predicate"))
    if predicate == RETRYABLE_RUNTIME_BUNDLE_PREDICATE:
        return
    if predicate != LEGACY_RUNTIME_BUNDLE_PREDICATE:
        raise ValueError("GOOGLE_AUTH_ONLY runtime-bundle index predicate is unknown")

    cursor.execute("LOCK TABLE public.release_activations IN SHARE ROW EXCLUSIVE MODE")
    cursor.execute(f"DROP INDEX public.{RUNTIME_BUNDLE_INDEX}")
    cursor.execute(
        f"""
        CREATE UNIQUE INDEX {RUNTIME_BUNDLE_INDEX}
        ON public.release_activations (environment, kind, runtime_bundle_id)
        WHERE runtime_bundle_id IS NOT NULL
          AND NOT (kind = 'GOOGLE_AUTH_ONLY' AND phase = 'CLEANED')
        """
    )
    repaired = _runtime_bundle_index(cursor)
    if (
        _normalized_index_predicate(repaired.get("predicate"))
        != RETRYABLE_RUNTIME_BUNDLE_PREDICATE
    ):
        raise ValueError("GOOGLE_AUTH_ONLY runtime-bundle index repair failed")


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
                (PRODUCTION_ACTIVATION_FENCE,),
            )
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions = tuple(str(row["version_num"]) for row in cursor.fetchall())
            if revisions != (SCHEMA_REVISION,):
                raise ValueError("production schema is not exactly 20260710_0021")
            _require_all_flags_off(cursor)
            _ensure_retryable_runtime_bundle_index(cursor)
            cursor.execute(
                """
                SELECT id, phase, report_sha256, api_deployment_id
                FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND (
                    runtime_bundle_id = %s OR source_sha = %s
                    OR api_deployment_id = %s
                  )
                ORDER BY created_at, id
                FOR UPDATE
                """,
                (
                    KIND,
                    coordinates["runtime_bundle_id"],
                    coordinates["source_sha"],
                    coordinates["deployment_id"],
                ),
            )
            _require_retryable_release_history(
                [dict(row) for row in cursor.fetchall()],
                deployment_id=coordinates["deployment_id"],
            )
            activation_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO release_activations (
                    id, environment, kind, source_sha, runtime_bundle_id,
                    manifest_sha256, api_deployment_id, api_deployment_url,
                    api_role, workflow_run_id, workflow_attempt, phase,
                    phase_rank, version, approval, reservation_expires_at
                ) VALUES (
                    %s, 'production', %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'ACCEPTANCE_READY', 1, 1, %s, CURRENT_TIMESTAMP + INTERVAL '30 minutes'
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
        "reservation_expires_at": activation["reservation_expires_at"].isoformat(),
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

    _validate_completion_evidence(
        completion_evidence,
        source_sha=source_sha,
        deployment_id=deployment_id,
    )
    report_sha256 = hashlib.sha256(completion_evidence).hexdigest()
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (PRODUCTION_ACTIVATION_FENCE,),
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


def _validate_completion_evidence(
    completion_evidence: bytes,
    *,
    source_sha: str,
    deployment_id: str,
) -> dict[str, Any]:
    if not completion_evidence or len(completion_evidence) > 64 * 1024:
        raise ValueError("GOOGLE_AUTH_ONLY completion evidence is invalid")
    try:
        payload = json.loads(completion_evidence.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("GOOGLE_AUTH_ONLY completion evidence is invalid") from error
    if not isinstance(payload, dict) or payload.get("passed") is not True:
        raise ValueError("GOOGLE_AUTH_ONLY completion evidence did not pass")
    if payload.get("source_sha") != source_sha or payload.get("deployment_id") != deployment_id:
        raise ValueError("GOOGLE_AUTH_ONLY completion evidence coordinates mismatch")
    schema = payload.get("schema")
    if schema == "vowpic.google-auth-only-final-state.v1":
        valid = (
            payload.get("database_all_off") is True
            and payload.get("public_all_off") is True
            and payload.get("active_unused_bindings") == 0
            and payload.get("active_acceptance_sessions") == 0
            and payload.get("oauth_intent_status_after") == 503
        )
    elif schema == "vowpic.google-auth-only-watchdog-evidence.v1":
        valid = (
            payload.get("sessions_zero") is True
            and payload.get("bindings_zero") is True
        )
    else:
        valid = False
    if not valid:
        raise ValueError("GOOGLE_AUTH_ONLY completion evidence is incomplete")
    return payload


def _load_release_module(name: str) -> Any:
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"vowpic_release_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load release helper {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reap_expired_activations(
    database_url: str,
    *,
    activation_plan: dict[str, Any],
) -> dict[str, Any]:
    """Idempotently restore OFF and clean every expired Google acceptance lease."""

    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (PRODUCTION_ACTIVATION_FENCE,),
            )
            cursor.execute(
                """
                SELECT source_sha, api_deployment_id, approval
                FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND phase = 'ACCEPTANCE_READY'
                  AND reservation_expires_at IS NOT NULL
                  AND reservation_expires_at <= CURRENT_TIMESTAMP
                ORDER BY created_at, id
                """,
                (KIND,),
            )
            expired = [dict(row) for row in cursor.fetchall()]

    apply_module = _load_release_module("apply_activation_plan")
    session_module = _load_release_module("cleanup_google_auth_sessions")
    binding_module = _load_release_module("cleanup_acceptance_bindings")
    reaped: list[dict[str, str]] = []
    for activation in expired:
        source_sha = str(activation["source_sha"])
        deployment_id = str(activation["api_deployment_id"])
        approval = str(activation["approval"])
        apply_module.apply_phase(
            database_url,
            phase="emergency-off",
            plan=activation_plan,
            approval=approval,
            kind=KIND,
            deployment_id=deployment_id,
            source_sha=source_sha,
            binding_report=None,
        )
        session_report = session_module.cleanup_sessions(
            database_url,
            deployment_id=deployment_id,
            approval=approval,
        )
        binding_report = binding_module.cleanup_bindings(
            database_url,
            deployment_id=deployment_id,
            approval=approval,
            kind=KIND,
            require_zero_unused=True,
        )
        if (
            session_report.get("passed") is not True
            or session_report.get("after_unrevoked") != 0
        ):
            raise RuntimeError("expired GOOGLE_AUTH_ONLY sessions remain after cleanup")
        if (
            binding_report.get("passed") is not True
            or binding_report.get("after", {}).get("unused_unrevoked") != 0
            or binding_report.get("after", {}).get("active_unused") != 0
        ):
            raise RuntimeError("expired GOOGLE_AUTH_ONLY bindings remain after cleanup")
        evidence = _canonical(
            {
                "schema": "vowpic.google-auth-only-watchdog-evidence.v1",
                "passed": True,
                "source_sha": source_sha,
                "deployment_id": deployment_id,
                "sessions_zero": True,
                "bindings_zero": True,
            }
        )
        complete_activation(
            database_url,
            source_sha=source_sha,
            deployment_id=deployment_id,
            approval=approval,
            completion_evidence=evidence,
        )
        reaped.append({"source_sha": source_sha, "deployment_id": deployment_id})
    return {
        "schema": "vowpic.google-auth-only-watchdog-report.v1",
        "passed": True,
        "reaped_count": len(reaped),
        "reaped": reaped,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reserve", "complete", "reap-expired"))
    parser.add_argument("--source-sha")
    parser.add_argument("--deployment-id")
    parser.add_argument("--base-url")
    parser.add_argument("--runtime-report")
    parser.add_argument("--completion-evidence")
    parser.add_argument("--activation-plan", default="release/activation-plan.json")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source_sha = str(args.source_sha or "").strip().lower()
        approval = os.environ.get(args.approval_id_env, "").strip()
        if args.action != "reap-expired" and not SOURCE_SHA.fullmatch(source_sha):
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
        elif args.action == "complete":
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
        else:
            plan = json.loads(Path(args.activation_plan).read_text(encoding="utf-8"))
            if not isinstance(plan, dict):
                raise ValueError("GOOGLE_AUTH_ONLY activation plan must be an object")
            report = reap_expired_activations(database_url, activation_plan=plan)
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
