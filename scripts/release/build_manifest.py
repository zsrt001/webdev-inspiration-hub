#!/usr/bin/env python3
"""Build a canonical, immutable, role-bound release bundle manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA = "vowpic.bundle-manifest.v1"
RELEASE_ROLES = frozenset(
    {"SAFE_BASELINE", "PREVIEW_IDENTITY", "PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"}
)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "release_role",
        "repository",
        "project_id",
        "runtime_bundle_id",
        "source_sha",
        "api_build_sha256",
        "api_deployment_id",
        "preview_id",
        "private_compatible_baseline_deployment_id",
        "staged_target_deployment_id",
        "worker_image_digest",
        "worker_deployment_id",
        "schema_revision",
        "api_compatibility_version",
        "worker_compatibility_version",
        "job_payload_min",
        "job_payload_max",
        "contract_hashes",
        "tool_versions",
    }
)
CONTRACT_HASH_FIELDS = frozenset(
    {
        "provider",
        "model",
        "policy",
        "catalog",
        "flag",
        "pre_activation_off_snapshot",
        "target_snapshot",
        "gate",
        "runtime",
    }
)
FORBIDDEN_MUTABLE_FIELDS = frozenset(
    {
        "observed_current_flag_snapshot_hash",
        "current_snapshot_hash",
        "current_feature_snapshot_hash",
        "report_sha256",
        "observation_result",
        "activation_result",
        "decision",
        "manifest_sha256",
        "evidence_sha256",
        "final_manifest_sha256",
        "live_state",
    }
)
_HEX = re.compile(r"^[0-9a-f]+$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_SCHEMA_REVISION = re.compile(r"^[0-9]{8}_[0-9]{4}$")
_OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


def _sha(value: Any, *, lengths: tuple[int, ...] = (64,), label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) not in lengths or not _HEX.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase hexadecimal digest")
    return text


def _identifier(value: Any, *, label: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{label} must be a bounded immutable identifier")
    return text


def _find_forbidden(value: Any, *, path: str = "manifest") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key in FORBIDDEN_MUTABLE_FIELDS:
                found.append(nested_path)
            found.extend(_find_forbidden(nested, path=nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_find_forbidden(nested, path=f"{path}[{index}]"))
    return found


def validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("bundle manifest must be an object")
    forbidden = _find_forbidden(payload)
    if forbidden:
        raise ValueError(f"mutable/future fields are forbidden: {', '.join(sorted(forbidden))}")
    missing = MANIFEST_FIELDS - set(payload)
    unexpected = set(payload) - MANIFEST_FIELDS
    if missing:
        raise ValueError(f"bundle manifest fields are missing: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"bundle manifest fields are not allowlisted: {', '.join(sorted(unexpected))}")
    if payload.get("schema") != SCHEMA:
        raise ValueError("bundle manifest schema is unsupported")
    role = str(payload.get("release_role") or "").strip()
    if role not in RELEASE_ROLES:
        raise ValueError("bundle manifest release role is unsupported")

    normalized = dict(payload)
    normalized["release_role"] = role
    normalized["repository"] = _identifier(payload["repository"], label="repository")
    normalized["project_id"] = _identifier(payload["project_id"], label="project ID")
    runtime_id = str(payload["runtime_bundle_id"] or "").strip().lower()
    if not _RUNTIME_ID.fullmatch(runtime_id):
        raise ValueError("runtime bundle ID is invalid")
    normalized["runtime_bundle_id"] = runtime_id
    normalized["source_sha"] = _sha(payload["source_sha"], lengths=(40, 64), label="source SHA")
    normalized["api_build_sha256"] = _sha(payload["api_build_sha256"], label="API build SHA")
    normalized["api_deployment_id"] = _identifier(
        payload["api_deployment_id"], label="API deployment ID"
    )
    normalized["preview_id"] = _identifier(payload["preview_id"], label="Preview ID", optional=True)
    normalized["private_compatible_baseline_deployment_id"] = _identifier(
        payload["private_compatible_baseline_deployment_id"],
        label="private-compatible baseline deployment ID",
        optional=True,
    )
    normalized["staged_target_deployment_id"] = _identifier(
        payload["staged_target_deployment_id"], label="staged-target deployment ID", optional=True
    )
    normalized["worker_deployment_id"] = _identifier(
        payload["worker_deployment_id"], label="Worker deployment ID", optional=True
    )

    worker_digest = payload["worker_image_digest"]
    if worker_digest is None:
        normalized["worker_image_digest"] = None
    else:
        digest = str(worker_digest).strip().lower()
        if not _OCI_DIGEST.fullmatch(digest):
            raise ValueError("Worker image must use an immutable OCI digest")
        normalized["worker_image_digest"] = digest

    schema_revision = str(payload["schema_revision"] or "").strip()
    if not _SCHEMA_REVISION.fullmatch(schema_revision):
        raise ValueError("schema revision must be a known Alembic revision shape")
    normalized["schema_revision"] = schema_revision
    for field in (
        "api_compatibility_version",
        "worker_compatibility_version",
        "job_payload_min",
        "job_payload_max",
    ):
        normalized[field] = _identifier(payload[field], label=field)

    contracts = payload["contract_hashes"]
    if not isinstance(contracts, dict) or set(contracts) != CONTRACT_HASH_FIELDS:
        raise ValueError("contract hashes must contain the exact immutable contract set")
    normalized["contract_hashes"] = {
        name: _sha(contracts[name], label=f"{name} contract SHA") for name in sorted(contracts)
    }
    tools = payload["tool_versions"]
    if not isinstance(tools, dict) or not tools:
        raise ValueError("pinned tool versions are required")
    normalized_tools: dict[str, str] = {}
    for name, version in sorted(tools.items()):
        clean_name = _identifier(name, label="tool name")
        clean_version = _identifier(version, label=f"{clean_name} version")
        normalized_tools[str(clean_name)] = str(clean_version)
    normalized["tool_versions"] = normalized_tools

    has_worker = role in {"PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"}
    if has_worker != bool(normalized["worker_image_digest"] and normalized["worker_deployment_id"]):
        raise ValueError("release role and Worker coordinates do not match")
    if role in {"PREVIEW_IDENTITY", "PREVIEW_COMMERCIAL"}:
        if not normalized["preview_id"]:
            raise ValueError("Preview role requires a Preview ID")
        if normalized["private_compatible_baseline_deployment_id"] or normalized["staged_target_deployment_id"]:
            raise ValueError("Preview manifest cannot contain Production deployment roles")
    elif role in {"COMMERCIAL_7A", "CONTRACT_7B"}:
        baseline = normalized["private_compatible_baseline_deployment_id"]
        target = normalized["staged_target_deployment_id"]
        if normalized["preview_id"] or not baseline or not target or baseline == target:
            raise ValueError("Production manifest requires two distinct Production deployments")
        if normalized["api_deployment_id"] != target:
            raise ValueError("Production API deployment must be the staged target")
    else:  # SAFE_BASELINE
        if normalized["preview_id"] or normalized["staged_target_deployment_id"]:
            raise ValueError("SAFE_BASELINE cannot contain Preview or staged-target coordinates")
        if normalized["private_compatible_baseline_deployment_id"] != normalized["api_deployment_id"]:
            raise ValueError("SAFE_BASELINE API must be its private-compatible baseline")
    return normalized


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    normalized = validate_manifest(payload)
    return (json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def write_manifest_create_once(path: Path, payload: dict[str, Any]) -> str:
    raw = canonical_manifest_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
    return hashlib.sha256(raw).hexdigest()


def _aware_timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_worker_report(
    manifest: dict[str, Any],
    report: dict[str, Any],
    *,
    now: datetime | None = None,
    maximum_age: timedelta = timedelta(seconds=120),
) -> None:
    normalized = validate_manifest(manifest)
    if normalized["release_role"] not in {"PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"}:
        raise ValueError("release role does not permit a Worker report")
    expected = {
        "schema": "vowpic.worker-runtime-report.v1",
        "release_role": normalized["release_role"],
        "source_sha": normalized["source_sha"],
        "runtime_bundle_id": normalized["runtime_bundle_id"],
        "worker_image_digest": normalized["worker_image_digest"],
        "worker_deployment_id": normalized["worker_deployment_id"],
        "schema_revision": normalized["schema_revision"],
        "job_payload_min": normalized["job_payload_min"],
        "job_payload_max": normalized["job_payload_max"],
    }
    if not isinstance(report, dict) or any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Worker report does not match the immutable bundle")
    observed = _aware_timestamp(report.get("published_at"), label="Worker published_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = current - observed
    if age < timedelta(seconds=-5) or age > maximum_age:
        raise ValueError("Worker heartbeat report is stale or from the future")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        digest = write_manifest_create_once(Path(args.output), payload)
        print(json.dumps({"manifest_sha256": digest, "path": args.output}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
