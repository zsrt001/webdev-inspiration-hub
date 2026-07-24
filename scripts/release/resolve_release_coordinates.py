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
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.github_artifact_evidence import (
    REFERENCE_PREFIX as GITHUB_ARTIFACT_REFERENCE_PREFIX,
    read_report as read_github_artifact_report,
)
from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest
from scripts.release.private_evidence_store import PrivateBlobEvidenceStore
from scripts.release.register_bundle import COMMERCIAL_7A_PHASES, phase_object_key

COORDINATE_KINDS = (
    "preview-identity",
    "preview-commercial",
    "preview-commercial-cleaned",
    "safe-baseline",
    "commercial-7a",
    "commercial-7a-failure",
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
    "commercial-7a": {"environment": "production", "kind": "COMMERCIAL_7A", "phase": "7A_ACCEPTED"},
    "commercial-7a-failure": {"environment": "production", "kind": "COMMERCIAL_7A", "phase": None},
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
    "private_compatible_baseline_deployment_id",
    "private_compatible_baseline_deployment_url",
    "staged_target_deployment_id", "staged_target_deployment_url",
)
_COMMERCIAL_7A_RANK = {
    phase: rank for rank, phase in enumerate(COMMERCIAL_7A_PHASES)
}


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("coordinate timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _expected_phase(coordinate_kind: str, requested: str | None) -> str:
    spec = SPEC_BY_KIND[coordinate_kind]
    if coordinate_kind == "commercial-7a-failure":
        if requested:
            raise ValueError("commercial-7a-failure resolves its durable current phase")
        return ""
    if coordinate_kind == "commercial-7a":
        phase = str(requested or spec["phase"]).strip()
        if phase not in _COMMERCIAL_7A_RANK:
            raise ValueError("COMMERCIAL_7A expected phase is not allowlisted")
        return phase
    if requested and requested != spec["phase"]:
        raise ValueError("expected phase override is only valid for its exact terminal phase")
    return spec["phase"]


def _exact_https_origin(value: object) -> str:
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
        raise ValueError("release deployment URL must be one exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _validate_commercial_phase_report(
    activation: dict[str, Any],
    report: dict[str, Any],
    *,
    expected_phase: str,
) -> dict[str, Any]:
    if (
        report.get("schema") != "vowpic.release-phase-report.v1"
        or str(report.get("activation_id")) != str(activation.get("id"))
        or report.get("environment") != "production"
        or report.get("kind") != "COMMERCIAL_7A"
        or report.get("source_sha") != activation.get("source_sha")
        or report.get("phase") != expected_phase
        or report.get("phase_rank") != _COMMERCIAL_7A_RANK[expected_phase]
    ):
        raise ValueError("COMMERCIAL_7A phase report identity mismatch")
    phase_evidence = report.get("phase_evidence")
    chain = report.get("evidence_chain")
    if (
        not isinstance(phase_evidence, dict)
        or phase_evidence.get("phase") != expected_phase
        or not isinstance(phase_evidence.get("coordinates"), dict)
        or not isinstance(chain, list)
        or not chain
        or chain[-1].get("phase") != expected_phase
        or chain[-1].get("phase_rank") != _COMMERCIAL_7A_RANK[expected_phase]
        or chain[-1].get("coordinates") != phase_evidence["coordinates"]
    ):
        raise ValueError("COMMERCIAL_7A phase evidence chain mismatch")
    return dict(phase_evidence["coordinates"])


def resolve_records(
    coordinate_kind: str,
    activations: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    now: datetime | None = None,
    maximum_age: timedelta = timedelta(hours=2),
    expected_source_sha: str | None = None,
    expected_phase: str | None = None,
    manifest_phase_coordinates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if coordinate_kind not in SPEC_BY_KIND:
        raise ValueError(f"coordinate kind is not allowlisted: {coordinate_kind}")
    if len(activations) != 1:
        raise ValueError("exactly one service-owned activation row is required")
    activation = dict(activations[0])
    if FORBIDDEN_ACTIVATION_KEYS & set(activation):
        raise ValueError("caller-authored authority claims are forbidden")
    spec = SPEC_BY_KIND[coordinate_kind]
    resolved_phase = _expected_phase(coordinate_kind, expected_phase)
    for key in ("environment", "kind"):
        if activation.get(key) != spec[key]:
            raise ValueError(f"activation {key} does not match {coordinate_kind}")
    if activation.get("phase") != resolved_phase:
        raise ValueError(f"activation phase does not match {coordinate_kind}")
    required = ("source_sha", "runtime_bundle_id", "api_deployment_id", "report_sha256", "updated_at")
    if coordinate_kind != "safe-baseline":
        required = (*required, "id", "api_deployment_url")
    commercial_kinds = {
        "preview-commercial", "preview-commercial-cleaned", "commercial-7a", "contract-7b"
    }
    if coordinate_kind in commercial_kinds:
        required = (
            *required,
            "manifest_sha256", "api_role", "private_evidence_prefix",
            "workflow_run_id", "workflow_attempt",
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

    if coordinate_kind == "commercial-7a":
        if _COMMERCIAL_7A_RANK[resolved_phase] < _COMMERCIAL_7A_RANK["MANIFEST_SEALED"]:
            raise ValueError("COMMERCIAL_7A coordinates are not sealed yet")
        if activation.get("api_role") != "COMMERCIAL_7A_API":
            raise ValueError("COMMERCIAL_7A API role binding mismatch")
        if any(
            activation.get(field) is not None
            for field in ("worker_deployment_id", "worker_role", "worker_image_digest")
        ):
            raise ValueError("COMMERCIAL_7A backend release contains Worker coordinates")
        report_hash = report.get("_content_sha256") or report.get("sha256")
        if report_hash != activation["report_sha256"]:
            raise ValueError("COMMERCIAL_7A current phase report hash mismatch")
        _validate_commercial_phase_report(
            activation, report, expected_phase=resolved_phase
        )
        manifest_coordinates = dict(manifest_phase_coordinates or {})
        expected_manifest_coordinates = {
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "staged_target_deployment_id": activation["api_deployment_id"],
            "staged_target_deployment_url": activation["api_deployment_url"],
        }
        for key, expected in expected_manifest_coordinates.items():
            if manifest_coordinates.get(key) != expected:
                raise ValueError(f"sealed manifest phase {key} mismatch")
        baseline_id = str(
            manifest_coordinates.get("private_compatible_baseline_deployment_id") or ""
        )
        target_id = str(manifest_coordinates.get("staged_target_deployment_id") or "")
        baseline_url = _exact_https_origin(
            manifest_coordinates.get("private_compatible_baseline_deployment_url")
        )
        target_url = _exact_https_origin(
            manifest_coordinates.get("staged_target_deployment_url")
        )
        if not baseline_id or baseline_id == target_id or baseline_url == target_url:
            raise ValueError("sealed baseline and target coordinates are not distinct")
        resolved = {
            key: activation.get(key)
            for key in OUTPUT_KEYS
            if key != "activation_id" and activation.get(key) is not None
        }
        resolved.update(
            {
                "worker_deployment_id": None,
                "worker_role": None,
                "worker_image_digest": None,
                "private_compatible_baseline_deployment_id": baseline_id,
                "private_compatible_baseline_deployment_url": baseline_url,
                "staged_target_deployment_id": target_id,
                "staged_target_deployment_url": target_url,
            }
        )
        resolved["activation_id"] = str(activation["id"])
        return resolved

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
    if coordinate_kind in commercial_kinds:
        resolved.update(
            {
                "worker_deployment_id": None,
                "worker_role": None,
                "worker_image_digest": None,
            }
        )
    if activation.get("id") is not None:
        resolved["activation_id"] = str(activation["id"])
    return resolved


def _load_activation(
    database_url: str,
    coordinate_kind: str,
    expected_source_sha: str | None = None,
    expected_phase: str | None = None,
) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    spec = SPEC_BY_KIND[coordinate_kind]
    if coordinate_kind == "commercial-7a-failure":
        with psycopg2.connect(database_url) as connection:
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT id, environment, kind, source_sha, runtime_bundle_id, manifest_sha256,
                           report_sha256, api_deployment_id, api_deployment_url, worker_deployment_id,
                           api_role, worker_role, worker_image_digest, phase, private_evidence_prefix,
                           workflow_run_id, workflow_attempt, updated_at
                    FROM release_activations
                    WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
                      AND phase NOT IN ('7A_ACCEPTED','FAILED','CLEANED')
                      {source_filter}
                    ORDER BY updated_at DESC LIMIT 2
                    """
                parameters: tuple[object, ...] = ()
                if expected_source_sha:
                    query = query.format(source_filter="AND source_sha = %s")
                    parameters = (expected_source_sha,)
                else:
                    query = query.format(source_filter="")
                cursor.execute(query, parameters)
                return [dict(row) for row in cursor.fetchall()]
    phase = _expected_phase(coordinate_kind, expected_phase)
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
                spec["environment"], spec["kind"], phase
            )
            if expected_source_sha:
                query = query.format(source_filter="AND source_sha = %s")
                parameters = (*parameters, expected_source_sha)
            else:
                query = query.format(source_filter="")
            cursor.execute(query, parameters)
            return [dict(row) for row in cursor.fetchall()]


def _load_phase_evidence_rows(
    database_url: str,
    *,
    activation_id: object,
    phases: tuple[str, ...],
) -> dict[str, dict[str, Any]] | None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(database_url) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT to_regclass('public.release_phase_evidence')")
            if cursor.fetchone()["to_regclass"] is None:
                return None
            cursor.execute(
                """
                SELECT phase, phase_rank, report_sha256, private_object_key,
                       coordinates_json
                FROM release_phase_evidence
                WHERE release_activation_id = %s AND phase = ANY(%s)
                ORDER BY phase_rank
                """,
                (activation_id, list(dict.fromkeys(phases))),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    return {str(row["phase"]): row for row in rows}


def _read_private_json(
    store: PrivateBlobEvidenceStore,
    *,
    object_key: str,
    expected_sha256: str,
) -> dict[str, Any]:
    raw = store.read(object_key)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError("Private Blob phase report hash mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Private Blob phase report must be a JSON object")
    return {**payload, "_content_sha256": actual}


def _read_commercial_phase_evidence(
    activation: dict[str, Any],
    *,
    database_url: str,
    store: PrivateBlobEvidenceStore,
    expected_phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
    rows = _load_phase_evidence_rows(
        database_url,
        activation_id=activation.get("id"),
        phases=(expected_phase, "MANIFEST_SEALED"),
    )
    current_key = phase_object_key(prefix, expected_phase)
    if rows is None:
        if expected_phase != "MANIFEST_SEALED":
            raise ValueError("release phase evidence table is required after schema 0020")
        current = _read_private_json(
            store,
            object_key=current_key,
            expected_sha256=str(activation.get("report_sha256") or ""),
        )
        manifest_coordinates = _validate_commercial_phase_report(
            activation, current, expected_phase=expected_phase
        )
        return current, manifest_coordinates

    required = {expected_phase, "MANIFEST_SEALED"}
    if set(rows) != required:
        raise ValueError("release phase evidence rows are incomplete or ambiguous")
    current_row = rows[expected_phase]
    if (
        current_row.get("phase_rank") != _COMMERCIAL_7A_RANK[expected_phase]
        or current_row.get("report_sha256") != activation.get("report_sha256")
        or current_row.get("private_object_key") != current_key
    ):
        raise ValueError("current release phase evidence row drift")
    current = _read_private_json(
        store,
        object_key=current_key,
        expected_sha256=str(current_row["report_sha256"]),
    )
    _validate_commercial_phase_report(
        activation, current, expected_phase=expected_phase
    )

    manifest_row = rows["MANIFEST_SEALED"]
    manifest_key = phase_object_key(prefix, "MANIFEST_SEALED")
    if (
        manifest_row.get("phase_rank") != _COMMERCIAL_7A_RANK["MANIFEST_SEALED"]
        or manifest_row.get("private_object_key") != manifest_key
    ):
        raise ValueError("sealed manifest phase evidence row drift")
    manifest = _read_private_json(
        store,
        object_key=manifest_key,
        expected_sha256=str(manifest_row.get("report_sha256") or ""),
    )
    manifest_coordinates = _validate_commercial_phase_report(
        activation, manifest, expected_phase="MANIFEST_SEALED"
    )
    stored_coordinates = manifest_row.get("coordinates_json")
    if isinstance(stored_coordinates, str):
        stored_coordinates = json.loads(stored_coordinates)
    if stored_coordinates != manifest_coordinates:
        raise ValueError("sealed manifest phase coordinates drift")
    return current, manifest_coordinates


def _read_sealed_manifest(
    activation: dict[str, Any], store: PrivateBlobEvidenceStore
) -> bytes:
    prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
    raw = store.read(f"{prefix}/00-bundle-manifest.json")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != activation.get("manifest_sha256"):
        raise ValueError("sealed release manifest hash mismatch")
    payload = validate_manifest(json.loads(raw.decode("utf-8")))
    if canonical_manifest_bytes(payload) != raw:
        raise ValueError("sealed release manifest is not canonical")
    return raw


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
    parser.add_argument("--expected-phase")
    parser.add_argument("--private-evidence-root-env")
    parser.add_argument(
        "--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID"
    )
    parser.add_argument(
        "--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN"
    )
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--source-sha")
    parser.add_argument("--maximum-age-seconds", type=int, default=7200)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output")
    parser.add_argument("--job-env")
    parser.add_argument("--env-prefix", default="RELEASE_")
    args = parser.parse_args()
    if args.database_url_env not in os.environ or not os.environ[args.database_url_env].strip():
        raise ValueError("database URL environment variable is absent")
    _reject_inherited_coordinates(args.env_prefix)
    if args.coordinate_kind == "commercial-7a" and not args.expected_phase:
        raise ValueError("commercial-7a resolution requires --expected-phase")
    activations = _load_activation(
        os.environ[args.database_url_env],
        args.coordinate_kind,
        args.source_sha,
        args.expected_phase,
    )
    if len(activations) != 1:
        raise ValueError("exactly one activation row is required")
    root_value = (
        os.environ.get(args.private_evidence_root_env, "").strip()
        if args.private_evidence_root_env
        else ""
    )
    manifest_coordinates = None
    if args.coordinate_kind in {"commercial-7a", "commercial-7a-failure"}:
        resolved_expected_phase = (
            args.expected_phase
            if args.coordinate_kind == "commercial-7a"
            else str(activations[0].get("phase") or "")
        )
        if resolved_expected_phase not in _COMMERCIAL_7A_RANK:
            raise ValueError("COMMERCIAL_7A failure phase is invalid")
        store = PrivateBlobEvidenceStore(
            store_id=os.environ.get(args.private_evidence_store_id_env, ""),
            token=os.environ.get(args.private_evidence_token_env, ""),
        )
        report, manifest_coordinates = _read_commercial_phase_evidence(
            activations[0],
            database_url=os.environ[args.database_url_env],
            store=store,
            expected_phase=resolved_expected_phase,
        )
        if args.manifest_output:
            manifest_output = Path(args.manifest_output)
            manifest_output.parent.mkdir(parents=True, exist_ok=True)
            with manifest_output.open("xb") as handle:
                handle.write(_read_sealed_manifest(activations[0], store))
    else:
        if args.manifest_output:
            raise ValueError("--manifest-output is only valid for COMMERCIAL_7A")
        report = _read_report(
            activations[0],
            Path(root_value) if root_value else None,
            github_token=os.environ.get(args.github_token_env, "").strip(),
        )
    resolution_kind = (
        "commercial-7a" if args.coordinate_kind == "commercial-7a-failure" else args.coordinate_kind
    )
    resolved = resolve_records(
        resolution_kind,
        activations,
        report,
        maximum_age=timedelta(seconds=max(1, args.maximum_age_seconds)),
        expected_source_sha=args.source_sha,
        expected_phase=(
            str(activations[0].get("phase") or "")
            if args.coordinate_kind == "commercial-7a-failure"
            else args.expected_phase
        ),
        manifest_phase_coordinates=manifest_coordinates,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(resolved, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if args.job_env:
        _write_job_env(Path(args.job_env), args.env_prefix, resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
