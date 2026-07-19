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
from scripts.release.private_evidence_store import PrivateBlobEvidenceStore
from scripts.release.verify_inventory_signature import verify_inventory_evidence


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

COMMERCIAL_7A_PHASES = (
    "RESERVED",
    "WORKER_STAGED",
    "API_BASELINE_STAGED",
    "API_TARGET_STAGED",
    "MANIFEST_SEALED",
    "SCHEMA_0020",
    "WORKER_RUNNING",
    "BASELINE_PROMOTED",
    "DATA_SWITCHED",
    "WORKER_DISPATCH_ENABLED",
    "TARGET_ACCEPTED",
    "TARGET_PROMOTED",
    "PUBLIC_INVALIDATED",
    "ACTIVATED",
    "OBSERVING",
    "7A_ACCEPTED",
)
_PHASE_RANK = {phase: rank for rank, phase in enumerate(COMMERCIAL_7A_PHASES)}
_EVIDENCE_FAILURES = {"FAIL", "FAILED", "NOT_RUN", "BLOCKED", "REJECTED"}
_SENSITIVE_EVIDENCE_KEY = re.compile(
    r"(email|token|cookie|password|secret|authorization|raw_url|object_key|permanent_url)",
    re.IGNORECASE,
)
_EVIDENCE_EMAIL = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE
)
_EVIDENCE_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)


def validate_production_phase_transition(kind: str, expected: str, target: str) -> None:
    if kind != "COMMERCIAL_7A":
        raise ValueError("only the COMMERCIAL_7A state machine is implemented")
    if expected not in _PHASE_RANK or target not in _PHASE_RANK:
        raise ValueError("Production release phase is not allowlisted")
    if _PHASE_RANK[target] != _PHASE_RANK[expected] + 1:
        raise ValueError("Production release phase must advance exactly one step")


def _reject_sensitive_evidence(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            denial_assertion = child is True and str(key).endswith("_denied")
            if _SENSITIVE_EVIDENCE_KEY.search(str(key)) and not denial_assertion:
                raise ValueError(
                    f"release evidence contains a sensitive field: {'.'.join((*path, str(key)))}"
                )
            _reject_sensitive_evidence(child, (*path, str(key)))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_evidence(child, (*path, str(index)))
        return
    if isinstance(value, str) and (
        _EVIDENCE_EMAIL.search(value) or _EVIDENCE_JWT.search(value)
    ):
        raise ValueError("release evidence contains a sensitive value")


def _validate_evidence_payload(value: Any, *, label: str) -> None:
    _reject_sensitive_evidence(value)
    if not isinstance(value, dict):
        return
    if value.get("passed") is False:
        raise ValueError(f"release evidence failed: {label}")
    for field in ("status", "decision", "result"):
        if str(value.get(field) or "").upper() in _EVIDENCE_FAILURES:
            raise ValueError(f"release evidence failed: {label}")


def _hash_regular_file(path: Path, *, label: str) -> dict[str, Any]:
    stat = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"release evidence must be a regular file: {label}")
    if stat.st_size < 1 or stat.st_size > 1_000_000_000:
        raise ValueError(f"release evidence size is invalid: {label}")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = raw.decode("utf-8", errors="strict")
        if _EVIDENCE_EMAIL.search(text) or _EVIDENCE_JWT.search(text):
            raise ValueError(f"release evidence contains a sensitive value: {label}")
    else:
        _validate_evidence_payload(payload, label=label)
    return {
        "kind": "file",
        "size": stat.st_size,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _hash_evidence_path(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"release evidence is missing: {label}")
    if resolved.is_file():
        return _hash_regular_file(resolved, label=label)
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError(f"release evidence path is invalid: {label}")
    entries: list[dict[str, Any]] = []
    for candidate in sorted(resolved.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_dir() and not candidate.is_symlink():
            continue
        relative = candidate.relative_to(resolved).as_posix()
        hashed = _hash_regular_file(candidate, label=f"{label}/{relative}")
        entries.append({"path": relative, **hashed})
        if len(entries) > 20_000:
            raise ValueError(f"release evidence directory is too large: {label}")
    if not entries:
        raise ValueError(f"release evidence directory is empty: {label}")
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "kind": "directory",
        "file_count": len(entries),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def build_phase_evidence(
    *,
    phase: str,
    evidence: dict[str, Path],
    coordinates: dict[str, Any],
) -> dict[str, Any]:
    if phase not in _PHASE_RANK or phase == "RESERVED":
        raise ValueError("release evidence phase is invalid")
    if not evidence:
        raise ValueError("release phase requires durable evidence")
    normalized_coordinates = {
        str(key): value for key, value in sorted(coordinates.items()) if value is not None
    }
    _reject_sensitive_evidence(normalized_coordinates)
    items = {
        label: _hash_evidence_path(Path(path), label=label)
        for label, path in sorted(evidence.items())
    }
    payload = {
        "schema": "vowpic.release-phase-evidence.v1",
        "phase": phase,
        "phase_rank": _PHASE_RANK[phase],
        "coordinates": normalized_coordinates,
        "evidence": items,
    }
    return {
        **payload,
        "phase_evidence_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def phase_object_key(prefix: str, phase: str) -> str:
    if phase not in _PHASE_RANK or phase == "RESERVED":
        raise ValueError("release phase object key is invalid")
    clean_prefix = str(prefix or "").strip().strip("/\\")
    if (
        not clean_prefix
        or ".." in Path(clean_prefix).parts
        or "latest" in {part.lower() for part in Path(clean_prefix).parts}
    ):
        raise ValueError("release evidence prefix is invalid")
    slug = phase.lower().replace("_", "-")
    return f"{clean_prefix}/phases/{_PHASE_RANK[phase]:02d}-{slug}.json"


def build_chained_phase_report(
    *,
    activation: dict[str, Any],
    phase_evidence: dict[str, Any],
    private_evidence_prefix: str,
    previous_report: dict[str, Any] | None,
) -> tuple[dict[str, Any], bytes, str]:
    phase = str(phase_evidence.get("phase") or "")
    if phase not in _PHASE_RANK or phase == "RESERVED":
        raise ValueError("chained release phase is invalid")
    evidence_sha = str(phase_evidence.get("phase_evidence_sha256") or "")
    if not _SHA64.fullmatch(evidence_sha):
        raise ValueError("phase evidence digest is invalid")
    evidence_payload = dict(phase_evidence)
    del evidence_payload["phase_evidence_sha256"]
    if hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest() != evidence_sha:
        raise ValueError("phase evidence digest does not match its payload")
    chain: list[dict[str, Any]] = []
    previous_sha256 = None
    if previous_report is not None:
        if (
            previous_report.get("schema") != "vowpic.release-phase-report.v1"
            or previous_report.get("activation_id") != str(activation.get("id") or "")
            or previous_report.get("source_sha") != activation.get("source_sha")
            or previous_report.get("kind") != activation.get("kind")
            or not isinstance(previous_report.get("evidence_chain"), list)
        ):
            raise ValueError("previous release phase report identity is invalid")
        chain = [dict(item) for item in previous_report["evidence_chain"]]
        if not chain or chain[-1].get("phase") != activation.get("phase"):
            raise ValueError("previous release phase chain does not end at the current phase")
        previous_raw = json.dumps(
            previous_report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        previous_sha256 = hashlib.sha256(previous_raw).hexdigest()
        if previous_sha256 != activation.get("report_sha256"):
            raise ValueError("previous release phase report hash drift")
        if chain[-1].get("report_sha256") not in {None, previous_sha256}:
            raise ValueError("previous release phase chain hash drift")
        chain[-1]["report_sha256"] = previous_sha256
    phase_object_key(private_evidence_prefix, phase)
    chain_entry = {
        "phase": phase,
        "phase_rank": _PHASE_RANK[phase],
        "phase_evidence_sha256": evidence_sha,
        "coordinates": evidence_payload["coordinates"],
    }
    if any(item.get("phase") == phase for item in chain):
        raise ValueError("release phase chain contains a duplicate phase")
    chain.append(chain_entry)
    unsigned = {
        "schema": "vowpic.release-phase-report.v1",
        "activation_id": str(activation.get("id") or ""),
        "environment": "production",
        "kind": str(activation.get("kind") or ""),
        "source_sha": str(activation.get("source_sha") or ""),
        "phase": phase,
        "phase_rank": _PHASE_RANK[phase],
        "previous_report_sha256": previous_sha256,
        "phase_evidence": evidence_payload,
        "evidence_chain": chain,
    }
    _reject_sensitive_evidence(unsigned)
    report = unsigned
    raw = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    return report, raw, hashlib.sha256(raw).hexdigest()


def decide_production_phase_advance(
    row: dict[str, Any],
    *,
    expected_phase: str,
    target_phase: str,
    evidence_sha256: str,
    bindings: dict[str, Any],
) -> dict[str, Any]:
    validate_production_phase_transition(
        str(row.get("kind") or ""), expected_phase, target_phase
    )
    if not _SHA64.fullmatch(str(evidence_sha256 or "")):
        raise ValueError("Production phase evidence SHA-256 is invalid")
    allowed_bindings = {
        "runtime_bundle_id",
        "worker_deployment_id",
        "worker_image_digest",
        "api_deployment_id",
        "api_deployment_url",
        "manifest_sha256",
        "current_snapshot_hash",
        "target_snapshot_hash",
        "private_evidence_prefix",
    }
    if set(bindings) - allowed_bindings:
        raise ValueError("Production phase bindings are not allowlisted")
    normalized = {key: value for key, value in bindings.items() if value is not None}
    for field, value in normalized.items():
        current = row.get(field)
        if current is not None and str(current) != str(value):
            raise ValueError(f"Production release immutable binding drift: {field}")
    current_phase = str(row.get("phase") or "")
    if current_phase == target_phase:
        if row.get("report_sha256") != evidence_sha256:
            raise ValueError("Production release phase evidence drift")
        for field, value in normalized.items():
            if str(row.get(field)) != str(value):
                raise ValueError(f"Production release replay binding drift: {field}")
        return {
            "state": "ALREADY_ADVANCED",
            "activation_id": str(row.get("id") or ""),
            "phase": target_phase,
            "phase_rank": _PHASE_RANK[target_phase],
            "version": int(row.get("version") or 0),
        }
    if current_phase != expected_phase:
        raise ValueError(
            f"Production release phase mismatch: expected {expected_phase}, found {current_phase}"
        )
    if int(row.get("phase_rank") or 0) != _PHASE_RANK[expected_phase]:
        raise ValueError("Production release phase rank drift")
    return {
        "state": "ADVANCE",
        "updates": {
            **normalized,
            "phase": target_phase,
            "phase_rank": _PHASE_RANK[target_phase],
            "report_sha256": evidence_sha256,
        },
    }


def read_production_activation_phase(
    database_url: str,
    *,
    kind: str,
    source_sha: str,
    expected_phase: str,
    target_phase: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    source = str(source_sha or "").strip().lower()
    if not _SOURCE_SHA.fullmatch(source):
        raise ValueError("Production phase source SHA is invalid")
    validate_production_phase_transition(kind, expected_phase, target_phase)
    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, environment, kind, source_sha, runtime_bundle_id,
                       manifest_sha256, report_sha256, api_deployment_id,
                       api_deployment_url, worker_deployment_id,
                       worker_image_digest, private_evidence_prefix,
                       workflow_run_id, workflow_attempt,
                       current_snapshot_hash, target_snapshot_hash,
                       phase, phase_rank, version
                FROM release_activations
                WHERE environment = 'production' AND kind = %s AND source_sha = %s
                  AND phase IN (%s, %s)
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (kind, source, expected_phase, target_phase),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError("exactly one Production release phase row is required")
    return rows[0]


def read_production_activation_exact(
    database_url: str,
    *,
    kind: str,
    source_sha: str,
    phase: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    source = str(source_sha or "").strip().lower()
    if kind != "COMMERCIAL_7A" or not _SOURCE_SHA.fullmatch(source) or phase not in _PHASE_RANK:
        raise ValueError("exact Production activation coordinates are invalid")
    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND source_sha = %s AND phase = %s
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (kind, source, phase),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError("exactly one Production activation is required")
    return rows[0]


def advance_production_activation_cas(
    database_url: str,
    *,
    kind: str,
    source_sha: str,
    expected_phase: str,
    target_phase: str,
    evidence_sha256: str,
    bindings: dict[str, Any],
    approval: str,
    private_object_key: str,
    evidence_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    source = str(source_sha or "").strip().lower()
    if not _SOURCE_SHA.fullmatch(source):
        raise ValueError("Production phase source SHA is invalid")
    clean_approval = str(approval or "").strip()
    if not clean_approval or len(clean_approval) > 160:
        raise ValueError("Production phase approval reference is required")
    validate_production_phase_transition(kind, expected_phase, target_phase)
    prefix = str(bindings.get("private_evidence_prefix") or "")
    if private_object_key != phase_object_key(prefix, target_phase):
        raise ValueError("Production phase Private Blob key mismatch")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-release",),
            )
            cursor.execute(
                """
                SELECT id, kind, source_sha, runtime_bundle_id, manifest_sha256,
                       report_sha256, api_deployment_id, api_deployment_url,
                       worker_deployment_id, worker_image_digest, private_evidence_prefix,
                       current_snapshot_hash, target_snapshot_hash,
                       phase, phase_rank, version
                FROM release_activations
                WHERE environment = 'production' AND kind = %s AND source_sha = %s
                  AND phase IN (%s, %s)
                ORDER BY updated_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (kind, source, expected_phase, target_phase),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if len(rows) != 1:
                raise ValueError("exactly one Production release phase row is required")
            row = rows[0]
            decision = decide_production_phase_advance(
                row,
                expected_phase=expected_phase,
                target_phase=target_phase,
                evidence_sha256=evidence_sha256,
                bindings=bindings,
            )
            if decision["state"] == "ALREADY_ADVANCED":
                cursor.execute(
                    "SELECT to_regclass('public.release_phase_evidence')"
                )
                if cursor.fetchone()["to_regclass"] is not None:
                    _persist_phase_evidence_chain(
                        cursor,
                        activation_id=row["id"],
                        prefix=str(row.get("private_evidence_prefix") or ""),
                        evidence_chain=evidence_chain,
                        current_phase=target_phase,
                        current_report_sha256=evidence_sha256,
                    )
                return decision
            updates = decision["updates"]
            allowed_columns = {
                "runtime_bundle_id",
                "worker_deployment_id",
                "worker_image_digest",
                "api_deployment_id",
                "api_deployment_url",
                "manifest_sha256",
                "current_snapshot_hash",
                "target_snapshot_hash",
                "private_evidence_prefix",
                "phase",
                "phase_rank",
                "report_sha256",
            }
            if set(updates) - allowed_columns:
                raise ValueError("Production phase update columns are not allowlisted")
            assignments = ", ".join(f"{column} = %s" for column in updates)
            parameters = [updates[column] for column in updates]
            parameters.extend([row["id"], row["version"], expected_phase])
            cursor.execute(
                f"""
                UPDATE release_activations
                SET {assignments}, version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND version = %s AND phase = %s
                RETURNING id, phase, phase_rank, version, report_sha256
                """,
                parameters,
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("Production phase CAS lost its fence")
            cursor.execute("SELECT to_regclass('public.release_phase_evidence')")
            if cursor.fetchone()["to_regclass"] is not None:
                _persist_phase_evidence_chain(
                    cursor,
                    activation_id=row["id"],
                    prefix=str(updates.get("private_evidence_prefix") or row.get("private_evidence_prefix") or ""),
                    evidence_chain=evidence_chain,
                    current_phase=target_phase,
                    current_report_sha256=evidence_sha256,
                )
            return {
                "state": "ADVANCED",
                "activation_id": str(updated["id"]),
                "phase": updated["phase"],
                "phase_rank": updated["phase_rank"],
                "version": updated["version"],
                "report_sha256": updated["report_sha256"],
                "private_object_key": private_object_key,
            }


def _persist_phase_evidence_chain(
    cursor: Any,
    *,
    activation_id: Any,
    prefix: str,
    evidence_chain: list[dict[str, Any]],
    current_phase: str,
    current_report_sha256: str,
) -> None:
    if not evidence_chain or evidence_chain[-1].get("phase") != current_phase:
        raise ValueError("release phase evidence chain does not end at the CAS phase")
    for item in evidence_chain:
        phase = str(item.get("phase") or "")
        rank = item.get("phase_rank")
        report_sha = str(item.get("report_sha256") or "")
        if phase == current_phase and not report_sha:
            report_sha = current_report_sha256
        if (
            phase not in _PHASE_RANK
            or rank != _PHASE_RANK[phase]
            or not _SHA64.fullmatch(report_sha)
            or not isinstance(item.get("coordinates"), dict)
        ):
            raise ValueError("release phase evidence chain entry is invalid")
        object_key = phase_object_key(prefix, phase)
        cursor.execute(
            """
            INSERT INTO release_phase_evidence (
                id, release_activation_id, phase, phase_rank, report_sha256,
                private_object_key, coordinates_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (release_activation_id, phase) DO NOTHING
            """,
            (
                str(uuid4()),
                activation_id,
                phase,
                rank,
                report_sha,
                object_key,
                json.dumps(item["coordinates"], sort_keys=True, separators=(",", ":")),
            ),
        )
        cursor.execute(
            """
            SELECT phase_rank, report_sha256, private_object_key, coordinates_json
            FROM release_phase_evidence
            WHERE release_activation_id = %s AND phase = %s
            FOR SHARE
            """,
            (activation_id, phase),
        )
        stored = cursor.fetchone()
        if (
            stored is None
            or stored["phase_rank"] != rank
            or stored["report_sha256"] != report_sha
            or stored["private_object_key"] != object_key
            or dict(stored["coordinates_json"]) != item["coordinates"]
        ):
            raise ValueError("release phase evidence append-only row drift")


def build_migration_parent_record(
    activation: dict[str, Any],
    *,
    inventory_sha256: str,
    approval: str,
    workflow_run_id: str,
    workflow_attempt: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        activation.get("environment") != "production"
        or activation.get("kind") != "COMMERCIAL_7A"
        or activation.get("phase") != "MANIFEST_SEALED"
        or not _RUNTIME_ID.fullmatch(str(activation.get("runtime_bundle_id") or ""))
        or not _SHA64.fullmatch(str(activation.get("manifest_sha256") or ""))
    ):
        raise ValueError("migration parent requires one sealed COMMERCIAL_7A activation")
    inventory = str(inventory_sha256 or "").strip().lower()
    if not _SHA64.fullmatch(inventory):
        raise ValueError("migration parent inventory SHA-256 is invalid")
    clean_approval = str(approval or "").strip()
    clean_run = str(workflow_run_id or "").strip()
    if not clean_approval or len(clean_approval) > 160 or not _RUN_ID.fullmatch(clean_run):
        raise ValueError("migration parent approval or workflow run is invalid")
    if not isinstance(workflow_attempt, int) or isinstance(workflow_attempt, bool) or workflow_attempt < 1:
        raise ValueError("migration parent workflow attempt is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "id": str(uuid4()),
        "parent_run_id": None,
        "release_activation_id": str(activation["id"]),
        "environment": "production",
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "manifest_sha256": activation["manifest_sha256"],
        "inventory_sha256": inventory,
        "script_sha256": None,
        "source_revision": None,
        "target_revision": "20260710_0020",
        "mode": "COMMERCIAL_7A_PARENT",
        "approval": clean_approval,
        "lease_owner": f"github:{clean_run}:{workflow_attempt}",
        "lease_expires_at": current + timedelta(hours=2),
        "heartbeat_at": current,
        "fencing_token": 1,
        "state": "ACTIVE",
        "counts_json": {
            "source_sha": activation["source_sha"],
            "workflow_run_id": clean_run,
            "workflow_attempt": workflow_attempt,
        },
    }


def decide_migration_parent(
    *,
    existing: list[dict[str, Any]],
    requested: dict[str, Any],
) -> dict[str, Any] | None:
    if len(existing) > 1:
        raise ValueError("migration parent state is ambiguous")
    if not existing:
        return None
    row = existing[0]
    comparable = (
        "release_activation_id",
        "environment",
        "runtime_bundle_id",
        "manifest_sha256",
        "inventory_sha256",
        "mode",
        "approval",
        "lease_owner",
        "target_revision",
    )
    if any(str(row.get(field)) != str(requested.get(field)) for field in comparable):
        raise ValueError("migration parent already exists with different coordinates")
    if row.get("state") not in {"ACTIVE", "COMPLETED"}:
        raise ValueError("migration parent is not reusable")
    return row


def bind_migration_parent_cas(
    database_url: str,
    *,
    requested: dict[str, Any],
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-release",),
            )
            cursor.execute(
                """
                SELECT * FROM data_migration_runs
                WHERE release_activation_id = %s AND parent_run_id IS NULL
                  AND mode = 'COMMERCIAL_7A_PARENT'
                ORDER BY created_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (requested["release_activation_id"],),
            )
            existing = [dict(row) for row in cursor.fetchall()]
            reused = decide_migration_parent(existing=existing, requested=requested)
            if reused is not None:
                return {
                    "state": "ALREADY_BOUND",
                    "migration_parent_run_id": str(reused["id"]),
                    "fencing_token": int(reused["fencing_token"]),
                }
            columns = tuple(requested)
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO data_migration_runs ({', '.join(columns)}) "
                f"VALUES ({placeholders}) RETURNING id, fencing_token",
                tuple(
                    json.dumps(requested[column], sort_keys=True, separators=(",", ":"))
                    if column == "counts_json"
                    else requested[column]
                    for column in columns
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                raise ValueError("migration parent insert returned no row")
            return {
                "state": "BOUND",
                "migration_parent_run_id": str(inserted["id"]),
                "fencing_token": int(inserted["fencing_token"]),
            }


_PHASE_EVIDENCE_ARGUMENTS = {
    "WORKER_STAGED": ("worker_build_report", "worker_deployment_report"),
    "API_BASELINE_STAGED": ("build_output", "inspect_report"),
    "API_TARGET_STAGED": ("build_output", "inspect_report"),
    "SCHEMA_0020": ("migration_report", "replay_report"),
    "WORKER_RUNNING": ("worker_start_report", "worker_heartbeat_report"),
    "BASELINE_PROMOTED": ("promotion_report",),
    "DATA_SWITCHED": (
        "identity_report",
        "commercial_report",
        "generation_report",
        "media_report",
    ),
    "WORKER_DISPATCH_ENABLED": ("worker_dispatch_report", "worker_heartbeat_report"),
    "TARGET_ACCEPTED": (
        "auth_security_report",
        "staged_acceptance_report",
        "subscription_acceptance_report",
        "provider_unknown_pause_report",
        "provider_unknown_resume_report",
        "provider_unknown_intent_report",
        "provider_unknown_state_report",
        "provider_unknown_disarm_report",
        "quality_report",
        "required_provider_grant_report",
        "auth_origin_add_report",
        "auth_origin_remove_report",
    ),
    "TARGET_PROMOTED": ("promotion_report", "worker_heartbeat_report"),
    "PUBLIC_INVALIDATED": ("delete_report", "private_media_report"),
    "ACTIVATED": ("activation_report",),
    "OBSERVING": ("observation_start_report",),
}


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    _validate_evidence_payload(payload, label=label)
    return payload


def _worker_bindings(build_report: Path, deployment_report: Path) -> dict[str, str]:
    build = _load_json_object(build_report, label="Worker build report")
    deployment = _load_json_object(deployment_report, label="Worker deployment report")
    for payload, action in ((build, "build-push"), (deployment, "deploy-suspended")):
        if (
            payload.get("schema") != "vowpic.worker-host-adapter-report.v1"
            or payload.get("action") != action
            or payload.get("passed") is not True
            or not isinstance(payload.get("coordinates"), dict)
        ):
            raise ValueError(f"approved Worker {action} report is invalid")
    build_coordinates = build["coordinates"]
    deployment_coordinates = deployment["coordinates"]
    digest = str(build_coordinates.get("worker_image_digest") or "").lower()
    deployment_digest = str(deployment_coordinates.get("worker_image_digest") or "").lower()
    worker_id = str(deployment_coordinates.get("worker_deployment_id") or "")
    runtime = str(deployment_coordinates.get("runtime_bundle_id") or "").lower()
    if not _IMAGE_DIGEST.fullmatch(digest) or digest != deployment_digest:
        raise ValueError("Worker build/deployment digest binding is invalid")
    if not _COORDINATE.fullmatch(worker_id):
        raise ValueError("Worker deployment ID is invalid")
    if not _RUNTIME_ID.fullmatch(runtime):
        raise ValueError("Worker deployment runtime bundle ID is invalid")
    return {
        "runtime_bundle_id": runtime,
        "worker_deployment_id": worker_id,
        "worker_image_digest": digest,
    }


def _deployment_id_from_report(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    candidates = set(re.findall(r"\bdpl_[A-Za-z0-9_-]{3,156}\b", raw))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        stack: list[Any] = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"deployment_id", "api_deployment_id", "id"}:
                        text = str(child or "")
                        if text.startswith("dpl_"):
                            candidates.add(text)
                    stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
    if len(candidates) != 1:
        raise ValueError("deployment inspect report must contain exactly one deployment ID")
    deployment_id = next(iter(candidates))
    if not _COORDINATE.fullmatch(deployment_id):
        raise ValueError("deployment ID is invalid")
    return deployment_id


def _canonical_report_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"


def _read_report_with_hash(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    if not raw or len(raw) > 1_000_000 or path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be one bounded regular file")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    _validate_evidence_payload(payload, label=label)
    return payload, hashlib.sha256(raw).hexdigest()


def _validate_resolution_for_fault(
    payload: dict[str, Any], activation: dict[str, Any]
) -> None:
    expected = {
        "activation_id": str(activation["id"]),
        "environment": "production",
        "kind": "COMMERCIAL_7A",
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "worker_deployment_id": activation["worker_deployment_id"],
        "worker_image_digest": activation["worker_image_digest"],
        "phase": "WORKER_DISPATCH_ENABLED",
    }
    for key, value in expected.items():
        if str(payload.get(key)) != str(value):
            raise ValueError(f"fault release resolution {key} mismatch")


def build_acceptance_fault_intent(
    activation: dict[str, Any],
    *,
    request_sha256: str,
    max_provider_submits: int,
    max_cost_minor_units: int,
    expires_at: datetime,
) -> dict[str, Any]:
    if (
        activation.get("environment") != "production"
        or activation.get("kind") != "COMMERCIAL_7A"
        or activation.get("phase") != "WORKER_DISPATCH_ENABLED"
        or not _SHA64.fullmatch(str(request_sha256 or ""))
        or max_provider_submits != 1
        or not isinstance(max_cost_minor_units, int)
        or isinstance(max_cost_minor_units, bool)
        or max_cost_minor_units < 1
    ):
        raise ValueError("acceptance fault intent coordinates are invalid")
    runtime = str(activation.get("runtime_bundle_id") or "")
    worker_id = str(activation.get("worker_deployment_id") or "")
    worker_digest = str(activation.get("worker_image_digest") or "")
    if (
        not _RUNTIME_ID.fullmatch(runtime)
        or not _COORDINATE.fullmatch(worker_id)
        or not _IMAGE_DIGEST.fullmatch(worker_digest)
    ):
        raise ValueError("acceptance fault Worker binding is incomplete")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("acceptance fault expiry must be timezone-aware")
    identity = (
        f"{activation['id']}:{activation['workflow_run_id']}:"
        f"{activation['workflow_attempt']}:provider-response-drop"
    )
    intent_id = "afi_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    payload = {
        "schema": "vowpic.acceptance-fault-intent.v1",
        "passed": True,
        "state": "PREPARED",
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": runtime,
        "worker_deployment_id": worker_id,
        "worker_image_digest": worker_digest,
        "fault_intent_id": intent_id,
        "request_sha256": request_sha256,
        "max_provider_submits": 1,
        "max_cost_minor_units": max_cost_minor_units,
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
    }
    _reject_sensitive_evidence(payload)
    return payload


def _validate_fault_intent_report(
    payload: dict[str, Any], activation: dict[str, Any]
) -> None:
    expected = {
        "schema": "vowpic.acceptance-fault-intent.v1",
        "passed": True,
        "state": "PREPARED",
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "worker_deployment_id": activation["worker_deployment_id"],
        "worker_image_digest": activation["worker_image_digest"],
        "max_provider_submits": 1,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"acceptance fault intent {key} mismatch")
    if (
        payload.get("fault_intent_id") != activation.get("acceptance_fault_intent_id")
        or not _SHA64.fullmatch(str(payload.get("request_sha256") or ""))
        or not isinstance(payload.get("max_cost_minor_units"), int)
        or payload["max_cost_minor_units"] < 1
    ):
        raise ValueError("acceptance fault intent immutable binding mismatch")
    expiry = _aware_timestamp(payload.get("expires_at"))
    if expiry != _aware_timestamp(activation.get("acceptance_fault_expires_at")):
        raise ValueError("acceptance fault intent expiry mismatch")


def _validate_worker_fault_report(
    payload: dict[str, Any],
    *,
    action: str,
    activation: dict[str, Any],
    require_absence: bool = False,
    require_tombstone: bool = False,
) -> None:
    coordinates = payload.get("coordinates")
    if (
        payload.get("schema") != "vowpic.worker-host-adapter-report.v1"
        or payload.get("passed") is not True
        or payload.get("action") != action
        or not isinstance(coordinates, dict)
        or coordinates.get("fault_intent_id")
        != activation.get("acceptance_fault_intent_id")
        or coordinates.get("runtime_bundle_id") != activation.get("runtime_bundle_id")
        or coordinates.get("worker_deployment_id")
        != activation.get("worker_deployment_id")
    ):
        raise ValueError(f"Worker fault {action} report binding mismatch")
    if require_absence and (
        coordinates.get("rule_present") is not False
        or coordinates.get("runtime_rule_count") != 0
    ):
        raise ValueError("Worker fault rule absence was not proven")
    if require_tombstone and coordinates.get("tombstone_persisted") is not True:
        raise ValueError("Worker fault cleanup tombstone was not proven")


def _validate_worker_dispatch_report(
    payload: dict[str, Any], *, activation: dict[str, Any], expected_mode: str
) -> None:
    coordinates = payload.get("coordinates")
    if (
        payload.get("schema") != "vowpic.worker-host-adapter-report.v1"
        or payload.get("passed") is not True
        or payload.get("action") != "set-dispatch"
        or not isinstance(coordinates, dict)
        or coordinates.get("runtime_bundle_id") != activation.get("runtime_bundle_id")
        or coordinates.get("worker_deployment_id")
        != activation.get("worker_deployment_id")
        or coordinates.get("dispatch_mode") != expected_mode
    ):
        raise ValueError("Worker dispatch report binding mismatch")


def prepare_acceptance_fault_cas(
    database_url: str,
    *,
    activation_id: str,
    intent: dict[str, Any],
    intent_sha256: str,
    approval: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if not _SHA64.fullmatch(intent_sha256):
        raise ValueError("acceptance fault intent hash is invalid")
    clean_approval = str(approval or "").strip()
    if not clean_approval or len(clean_approval) > 160:
        raise ValueError("acceptance fault approval is required")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-release",),
            )
            cursor.execute(
                "SELECT * FROM release_activations WHERE id = %s FOR UPDATE",
                (activation_id,),
            )
            row = cursor.fetchone()
            if row is None or row["phase"] != "WORKER_DISPATCH_ENABLED":
                raise ValueError("acceptance fault release is unavailable")
            row = dict(row)
            if row.get("acceptance_fault_state") is not None:
                if (
                    row.get("acceptance_fault_intent_id") != intent["fault_intent_id"]
                    or row.get("acceptance_fault_intent_sha256") != intent_sha256
                    or _aware_timestamp(row.get("acceptance_fault_expires_at"))
                    != _aware_timestamp(intent["expires_at"])
                ):
                    raise ValueError("existing acceptance fault intent drift")
                return row
            cursor.execute(
                """
                UPDATE release_activations
                SET acceptance_fault_intent_id = %s,
                    acceptance_fault_intent_sha256 = %s,
                    acceptance_fault_state = 'PREPARED',
                    acceptance_fault_expires_at = %s,
                    version = version + 1
                WHERE id = %s AND version = %s
                  AND phase = 'WORKER_DISPATCH_ENABLED'
                  AND acceptance_fault_state IS NULL
                RETURNING *
                """,
                (
                    intent["fault_intent_id"],
                    intent_sha256,
                    intent["expires_at"],
                    row["id"],
                    row["version"],
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("acceptance fault prepare CAS lost its fence")
            return dict(updated)


def transition_acceptance_fault_cas(
    database_url: str,
    *,
    activation_id: str,
    expected_states: tuple[str, ...],
    target_state: str,
    intent_id: str,
    approval: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if target_state not in {"ARMED", "CLEANUP_CLAIMED", "DISARMED"}:
        raise ValueError("acceptance fault target state is invalid")
    if not str(approval or "").strip():
        raise ValueError("acceptance fault transition approval is required")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-release",),
            )
            cursor.execute(
                "SELECT * FROM release_activations WHERE id = %s FOR UPDATE",
                (activation_id,),
            )
            row = cursor.fetchone()
            if row is None or row["acceptance_fault_intent_id"] != intent_id:
                raise ValueError("acceptance fault intent does not match the release")
            row = dict(row)
            if row["acceptance_fault_state"] == target_state:
                return row
            if row["acceptance_fault_state"] not in expected_states:
                raise ValueError("acceptance fault state transition is invalid")
            cursor.execute(
                """
                UPDATE release_activations
                SET acceptance_fault_state = %s, version = version + 1
                WHERE id = %s AND version = %s AND acceptance_fault_state = %s
                RETURNING *
                """,
                (
                    target_state,
                    row["id"],
                    row["version"],
                    row["acceptance_fault_state"],
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("acceptance fault transition CAS lost its fence")
            return dict(updated)


def claim_acceptance_fault_cleanup_cas(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    approval: str,
) -> dict[str, Any] | None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if not _SOURCE_SHA.fullmatch(source_sha) or not _RUN_ID.fullmatch(workflow_run_id):
        raise ValueError("fault cleanup workflow coordinates are invalid")
    if workflow_attempt < 1 or not str(approval or "").strip():
        raise ValueError("fault cleanup approval or attempt is invalid")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-release",),
            )
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
                  AND source_sha = %s AND workflow_run_id = %s
                  AND workflow_attempt = %s
                ORDER BY updated_at DESC
                LIMIT 2
                FOR UPDATE
                """,
                (source_sha, workflow_run_id, workflow_attempt),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if not rows:
                return None
            if len(rows) != 1:
                raise ValueError("fault cleanup release state is ambiguous")
            row = rows[0]
            state = row.get("acceptance_fault_state")
            if state in {None, "DISARMED"}:
                return None
            if state == "CLEANUP_CLAIMED":
                return row
            if state not in {"PREPARED", "ARMED"}:
                raise ValueError("fault cleanup intent state is invalid")
            claim_id = "afc_" + hashlib.sha256(
                f"{row['acceptance_fault_intent_id']}:cleanup".encode("utf-8")
            ).hexdigest()[:32]
            fencing = int(row.get("acceptance_fault_cleanup_fencing_token") or 0) + 1
            cursor.execute(
                """
                UPDATE release_activations
                SET acceptance_fault_state = 'CLEANUP_CLAIMED',
                    acceptance_fault_cleanup_claim_id = %s,
                    acceptance_fault_cleanup_fencing_token = %s,
                    version = version + 1
                WHERE id = %s AND version = %s AND acceptance_fault_state = %s
                RETURNING *
                """,
                (claim_id, fencing, row["id"], row["version"], state),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("fault cleanup claim CAS lost its fence")
            return dict(updated)


def _prepare_acceptance_fault_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Prepare one durable Provider response-drop intent")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", required=True, choices=("WORKER_DISPATCH_ENABLED",))
    parser.add_argument("--request-report", required=True)
    parser.add_argument("--release-resolution-report", required=True)
    parser.add_argument("--max-provider-submits", required=True, type=int)
    parser.add_argument("--max-cost-minor-units", required=True, type=int)
    parser.add_argument("--expires-in-seconds", required=True, type=int)
    parser.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    parser.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.expires_in_seconds < 1 or args.expires_in_seconds > 300:
            raise ValueError("acceptance fault TTL must be between 1 and 300 seconds")
        request, request_sha = _read_report_with_hash(
            Path(args.request_report), label="Provider unknown request report"
        )
        resolution, _ = _read_report_with_hash(
            Path(args.release_resolution_report), label="release resolution report"
        )
        source_sha = str(request.get("source_sha") or "").strip().lower()
        activation = read_production_activation_exact(
            os.environ.get(args.database_url_env, ""),
            kind=args.kind,
            source_sha=source_sha,
            phase=args.expected_phase,
        )
        _validate_resolution_for_fault(resolution, activation)
        expected_request = {
            "schema": "vowpic.provider-unknown-canary.v1",
            "passed": True,
            "source_sha": activation["source_sha"],
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "worker_deployment_id": activation["worker_deployment_id"],
            "worker_image_digest": activation["worker_image_digest"],
        }
        for key, value in expected_request.items():
            if request.get(key) != value:
                raise ValueError(f"Provider unknown request {key} mismatch")
        prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
        provisional = build_acceptance_fault_intent(
            activation,
            request_sha256=request_sha,
            max_provider_submits=args.max_provider_submits,
            max_cost_minor_units=args.max_cost_minor_units,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=args.expires_in_seconds),
        )
        if request.get("fault_intent_id") != provisional["fault_intent_id"]:
            raise ValueError("Provider unknown request fault intent ID mismatch")
        object_key = f"{prefix}/fault-intents/{provisional['fault_intent_id']}.json"
        store = PrivateBlobEvidenceStore(
            store_id=os.environ.get(args.private_evidence_store_id_env, ""),
            token=os.environ.get(args.private_evidence_token_env, ""),
        )
        try:
            raw = store.read(object_key)
            intent = json.loads(raw.decode("utf-8"))
            if not isinstance(intent, dict) or _canonical_report_bytes(intent) != raw:
                raise ValueError("stored acceptance fault intent is not canonical")
            for key, value in provisional.items():
                if key != "expires_at" and intent.get(key) != value:
                    raise ValueError(f"stored acceptance fault intent {key} drift")
            expiry = _aware_timestamp(intent.get("expires_at"))
            current = datetime.now(timezone.utc)
            if expiry <= current or expiry > current + timedelta(seconds=300):
                raise ValueError("stored acceptance fault intent has expired or exceeds its TTL")
        except FileNotFoundError:
            intent = provisional
            raw = _canonical_report_bytes(intent)
            store.put_create_once(object_key, raw)
        intent_sha = hashlib.sha256(raw).hexdigest()
        updated = prepare_acceptance_fault_cas(
            os.environ.get(args.database_url_env, ""),
            activation_id=str(activation["id"]),
            intent=intent,
            intent_sha256=intent_sha,
            approval=os.environ.get(args.approval_id_env, ""),
        )
        if updated.get("acceptance_fault_intent_sha256") != intent_sha:
            raise ValueError("prepared acceptance fault intent hash drift")
        _write_create_once(Path(args.output), intent)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _confirm_acceptance_fault_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Confirm one armed Provider response-drop intent")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", required=True, choices=("WORKER_DISPATCH_ENABLED",))
    parser.add_argument("--fault-intent-report", required=True)
    parser.add_argument("--fault-report", required=True)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        intent, intent_sha = _read_report_with_hash(
            Path(args.fault_intent_report), label="fault intent report"
        )
        activation = read_production_activation_exact(
            os.environ.get(args.database_url_env, ""),
            kind=args.kind,
            source_sha=str(intent.get("source_sha") or ""),
            phase=args.expected_phase,
        )
        if intent_sha != activation.get("acceptance_fault_intent_sha256"):
            raise ValueError("fault intent report hash mismatch")
        _validate_fault_intent_report(intent, activation)
        fault, _ = _read_report_with_hash(Path(args.fault_report), label="fault arm report")
        _validate_worker_fault_report(
            fault, action="arm-response-drop-once", activation=activation
        )
        updated = transition_acceptance_fault_cas(
            os.environ.get(args.database_url_env, ""),
            activation_id=str(activation["id"]),
            expected_states=("PREPARED",),
            target_state="ARMED",
            intent_id=intent["fault_intent_id"],
            approval=os.environ.get(args.approval_id_env, ""),
        )
        result = {
            "schema": "vowpic.acceptance-fault-transition.v1",
            "passed": True,
            "activation_id": str(updated["id"]),
            "fault_intent_id": intent["fault_intent_id"],
            "state": "ARMED",
        }
        if args.output:
            _write_create_once(Path(args.output), result)
        else:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _claim_acceptance_fault_cleanup_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Claim cancel-safe fault cleanup")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True, type=int)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        row = claim_acceptance_fault_cleanup_cas(
            os.environ.get(args.database_url_env, ""),
            source_sha=args.source_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            approval=os.environ.get(args.approval_id_env, ""),
        )
        if row is None:
            report = {
                "schema": "vowpic.acceptance-fault-cleanup-claim.v1",
                "passed": True,
                "disposition": "NO_INTENT",
                "source_sha": args.source_sha,
                "workflow_run_id": args.workflow_run_id,
                "workflow_attempt": args.workflow_attempt,
            }
        else:
            report = {
                "schema": "vowpic.acceptance-fault-cleanup-claim.v1",
                "passed": True,
                "disposition": "INTENT_PRESENT",
                "activation_id": str(row["id"]),
                "source_sha": row["source_sha"],
                "runtime_bundle_id": row["runtime_bundle_id"],
                "worker_deployment_id": row["worker_deployment_id"],
                "worker_image_digest": row["worker_image_digest"],
                "fault_intent_id": row["acceptance_fault_intent_id"],
                "fault_intent_sha256": row["acceptance_fault_intent_sha256"],
                "cleanup_claim_id": row["acceptance_fault_cleanup_claim_id"],
                "cleanup_fencing_token": row["acceptance_fault_cleanup_fencing_token"],
            }
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _complete_acceptance_fault_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Complete normal or cleanup fault disarm")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", default="WORKER_DISPATCH_ENABLED")
    parser.add_argument("--fault-intent-report")
    parser.add_argument("--cleanup-claim-report")
    parser.add_argument("--dispatch-report")
    parser.add_argument("--disarm-report", required=True)
    parser.add_argument("--absence-report")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if bool(args.fault_intent_report) == bool(args.cleanup_claim_report):
            raise ValueError("exactly one fault intent or cleanup claim report is required")
        cleanup = bool(args.cleanup_claim_report)
        source_report, _ = _read_report_with_hash(
            Path(args.cleanup_claim_report or args.fault_intent_report),
            label="fault completion source report",
        )
        activation_id = str(source_report.get("activation_id") or "")
        source_sha = str(source_report.get("source_sha") or "")
        activation = read_production_activation_exact(
            os.environ.get(args.database_url_env, ""),
            kind=args.kind,
            source_sha=source_sha,
            phase=args.expected_phase,
        )
        if activation_id != str(activation["id"]):
            raise ValueError("fault completion activation mismatch")
        if cleanup:
            expected_claim = {
                "schema": "vowpic.acceptance-fault-cleanup-claim.v1",
                "passed": True,
                "disposition": "INTENT_PRESENT",
                "activation_id": str(activation["id"]),
                "source_sha": activation["source_sha"],
                "runtime_bundle_id": activation["runtime_bundle_id"],
                "worker_deployment_id": activation["worker_deployment_id"],
                "worker_image_digest": activation["worker_image_digest"],
                "fault_intent_id": activation["acceptance_fault_intent_id"],
                "fault_intent_sha256": activation["acceptance_fault_intent_sha256"],
                "cleanup_claim_id": activation["acceptance_fault_cleanup_claim_id"],
                "cleanup_fencing_token": activation[
                    "acceptance_fault_cleanup_fencing_token"
                ],
            }
            if any(source_report.get(key) != value for key, value in expected_claim.items()):
                raise ValueError("fault cleanup claim report binding mismatch")
            if not args.dispatch_report:
                raise ValueError("fault cleanup dispatch report is required")
            dispatch, _ = _read_report_with_hash(
                Path(args.dispatch_report), label="fault cleanup dispatch report"
            )
            _validate_worker_dispatch_report(
                dispatch, activation=activation, expected_mode="disabled"
            )
        intent_id = str(
            source_report.get("fault_intent_id")
            or activation.get("acceptance_fault_intent_id")
            or ""
        )
        disarm, _ = _read_report_with_hash(
            Path(args.disarm_report), label="fault disarm report"
        )
        _validate_worker_fault_report(
            disarm,
            action="disarm-response-drop",
            activation=activation,
            require_absence=True,
            require_tombstone=cleanup,
        )
        if args.absence_report:
            absence, _ = _read_report_with_hash(
                Path(args.absence_report), label="fault absence report"
            )
            _validate_worker_fault_report(
                absence,
                action="inspect-response-drop",
                activation=activation,
                require_absence=True,
                require_tombstone=cleanup,
            )
        updated = transition_acceptance_fault_cas(
            os.environ.get(args.database_url_env, ""),
            activation_id=str(activation["id"]),
            expected_states=("ARMED", "CLEANUP_CLAIMED"),
            target_state="DISARMED",
            intent_id=intent_id,
            approval=os.environ.get(args.approval_id_env, ""),
        )
        result = {
            "schema": "vowpic.acceptance-fault-transition.v1",
            "passed": True,
            "activation_id": str(updated["id"]),
            "fault_intent_id": intent_id,
            "state": "DISARMED",
            "cleanup": cleanup,
        }
        if args.output:
            _write_create_once(Path(args.output), result)
        else:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _advance_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Advance one COMMERCIAL_7A phase by CAS")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--runtime-bundle-id")
    parser.add_argument("--deployment-url")
    parser.add_argument("--deployment-role")
    parser.add_argument("--formal-base-url")
    parser.add_argument("--migration-parent-run-id")
    for argument in sorted({item for items in _PHASE_EVIDENCE_ARGUMENTS.values() for item in items}):
        parser.add_argument(f"--{argument.replace('_', '-')}")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    parser.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        validate_production_phase_transition(args.kind, args.expected_phase, args.phase)
        required_evidence = _PHASE_EVIDENCE_ARGUMENTS.get(args.phase)
        if required_evidence is None:
            raise ValueError("Production phase must use its dedicated command")
        evidence = {}
        for name in required_evidence:
            value = getattr(args, name)
            if not value:
                raise ValueError(f"Production phase requires --{name.replace('_', '-')}")
            evidence[name.replace("_", "-")] = Path(value)
        source_sha = str(args.source_sha or os.environ.get("SOURCE_SHA") or "").strip().lower()
        database_url = os.environ.get(args.database_url_env, "")
        activation = read_production_activation_phase(
            database_url,
            kind=args.kind,
            source_sha=source_sha,
            expected_phase=args.expected_phase,
            target_phase=args.phase,
        )
        prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
        if not prefix:
            prefix = (
                f"artifacts/release/{source_sha}/"
                f"{activation['workflow_run_id']}-{activation['workflow_attempt']}"
            )
        coordinates = {
            "source_sha": source_sha,
            "runtime_bundle_id": args.runtime_bundle_id,
            "deployment_url": args.deployment_url,
            "deployment_role": args.deployment_role,
            "formal_base_url": args.formal_base_url,
            "migration_parent_run_id": args.migration_parent_run_id,
        }
        bindings: dict[str, Any] = {}
        if args.phase == "WORKER_STAGED":
            bindings.update(
                _worker_bindings(
                    Path(args.worker_build_report), Path(args.worker_deployment_report)
                )
            )
            if args.runtime_bundle_id and args.runtime_bundle_id != bindings["runtime_bundle_id"]:
                raise ValueError("Worker runtime bundle argument/report mismatch")
        elif args.phase in {"API_BASELINE_STAGED", "API_TARGET_STAGED"}:
            expected_role = (
                "private-compatible-baseline"
                if args.phase == "API_BASELINE_STAGED"
                else "staged-target"
            )
            if args.deployment_role != expected_role:
                raise ValueError("Production deployment role is invalid for the phase")
            deployment_url = _exact_https_url(str(args.deployment_url or ""))
            deployment_id = _deployment_id_from_report(Path(args.inspect_report))
            coordinates["deployment_id"] = deployment_id
            coordinates["deployment_url"] = deployment_url
            if args.phase == "API_TARGET_STAGED":
                bindings.update(
                    {
                        "api_deployment_id": deployment_id,
                        "api_deployment_url": deployment_url,
                    }
                )
        if args.runtime_bundle_id:
            runtime = str(args.runtime_bundle_id).strip().lower()
            if not _RUNTIME_ID.fullmatch(runtime):
                raise ValueError("Production runtime bundle ID is invalid")
            bindings["runtime_bundle_id"] = runtime
        phase_evidence = build_phase_evidence(
            phase=args.phase,
            evidence=evidence,
            coordinates=coordinates,
        )
        store = PrivateBlobEvidenceStore(
            store_id=os.environ.get(args.private_evidence_store_id_env, ""),
            token=os.environ.get(args.private_evidence_token_env, ""),
        )
        current_object_key = phase_object_key(prefix, args.phase)
        if activation["phase"] == args.phase:
            report_raw = store.read(current_object_key)
            report_sha256 = hashlib.sha256(report_raw).hexdigest()
            if report_sha256 != activation.get("report_sha256"):
                raise ValueError("stored release phase report hash drift")
            phase_report = json.loads(report_raw.decode("utf-8"))
            expected_phase_payload = dict(phase_evidence)
            del expected_phase_payload["phase_evidence_sha256"]
            if phase_report.get("phase_evidence") != expected_phase_payload:
                raise ValueError("stored release phase evidence differs from this retry")
        else:
            previous_report = None
            if args.expected_phase != "RESERVED":
                previous_object_key = phase_object_key(prefix, args.expected_phase)
                previous_raw = store.read(previous_object_key)
                if hashlib.sha256(previous_raw).hexdigest() != activation.get("report_sha256"):
                    raise ValueError("previous Private Blob phase report hash drift")
                previous_report = json.loads(previous_raw.decode("utf-8"))
            phase_report, report_raw, report_sha256 = build_chained_phase_report(
                activation=activation,
                phase_evidence=phase_evidence,
                private_evidence_prefix=prefix,
                previous_report=previous_report,
            )
            store.put_create_once(current_object_key, report_raw)
        bindings["private_evidence_prefix"] = prefix
        result = advance_production_activation_cas(
            database_url,
            kind=args.kind,
            source_sha=source_sha,
            expected_phase=args.expected_phase,
            target_phase=args.phase,
            evidence_sha256=report_sha256,
            bindings=bindings,
            approval=os.environ.get(args.approval_id_env, ""),
            private_object_key=current_object_key,
            evidence_chain=phase_report["evidence_chain"],
        )
        output = {
            **result,
            "phase_evidence_sha256": phase_evidence["phase_evidence_sha256"],
            "report_sha256": report_sha256,
        }
        if args.output:
            _write_create_once(Path(args.output), output)
        else:
            print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _seal_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Seal one immutable COMMERCIAL_7A manifest")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", required=True, choices=("API_TARGET_STAGED",))
    parser.add_argument("--phase", required=True, choices=("MANIFEST_SEALED",))
    parser.add_argument("--source-sha")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--private-compatible-baseline-url", required=True)
    parser.add_argument("--staged-target-url", required=True)
    parser.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    parser.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        source_sha = str(args.source_sha or os.environ.get("SOURCE_SHA") or "").strip().lower()
        database_url = os.environ.get(args.database_url_env, "")
        activation = read_production_activation_phase(
            database_url,
            kind=args.kind,
            source_sha=source_sha,
            expected_phase=args.expected_phase,
            target_phase=args.phase,
        )
        manifest_path = Path(args.manifest)
        raw = manifest_path.read_bytes()
        manifest = validate_manifest(json.loads(raw.decode("utf-8")))
        if canonical_manifest_bytes(manifest) != raw:
            raise ValueError("Production manifest is not canonical")
        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if manifest_sha256 != str(args.expected_manifest_sha256 or "").lower():
            raise ValueError("Production manifest SHA-256 mismatch")
        expected = {
            "release_role": "COMMERCIAL_7A",
            "source_sha": source_sha,
            "runtime_bundle_id": activation.get("runtime_bundle_id"),
            "api_deployment_id": activation.get("api_deployment_id"),
            "staged_target_deployment_id": activation.get("api_deployment_id"),
            "worker_deployment_id": activation.get("worker_deployment_id"),
            "worker_image_digest": activation.get("worker_image_digest"),
            "schema_revision": "20260710_0020",
        }
        for field, value in expected.items():
            if manifest.get(field) != value:
                raise ValueError(f"Production manifest {field} binding mismatch")
        baseline_id = str(manifest.get("private_compatible_baseline_deployment_id") or "")
        target_id = str(manifest.get("staged_target_deployment_id") or "")
        if not _COORDINATE.fullmatch(baseline_id) or baseline_id == target_id:
            raise ValueError("Production manifest baseline/target identities are invalid")
        baseline_url = _exact_https_url(args.private_compatible_baseline_url)
        target_url = _exact_https_url(args.staged_target_url)
        if target_url != activation.get("api_deployment_url") or baseline_url == target_url:
            raise ValueError("Production manifest deployment URL binding mismatch")
        prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
        if not prefix:
            prefix = (
                f"artifacts/release/{source_sha}/"
                f"{activation['workflow_run_id']}-{activation['workflow_attempt']}"
            )
        store = PrivateBlobEvidenceStore(
            store_id=os.environ.get(args.private_evidence_store_id_env, ""),
            token=os.environ.get(args.private_evidence_token_env, ""),
        )
        manifest_object_key = f"{prefix}/00-bundle-manifest.json"
        store.put_create_once(manifest_object_key, raw)
        phase_evidence = build_phase_evidence(
            phase="MANIFEST_SEALED",
            evidence={"manifest": manifest_path},
            coordinates={
                "source_sha": source_sha,
                "runtime_bundle_id": manifest["runtime_bundle_id"],
                "manifest_sha256": manifest_sha256,
                "private_compatible_baseline_deployment_id": baseline_id,
                "private_compatible_baseline_deployment_url": baseline_url,
                "staged_target_deployment_id": target_id,
                "staged_target_deployment_url": target_url,
                "worker_deployment_id": manifest["worker_deployment_id"],
                "worker_image_digest": manifest["worker_image_digest"],
            },
        )
        current_object_key = phase_object_key(prefix, "MANIFEST_SEALED")
        if activation["phase"] == "MANIFEST_SEALED":
            report_raw = store.read(current_object_key)
            report_sha256 = hashlib.sha256(report_raw).hexdigest()
            if report_sha256 != activation.get("report_sha256"):
                raise ValueError("stored manifest phase report hash drift")
            phase_report = json.loads(report_raw.decode("utf-8"))
        else:
            previous_key = phase_object_key(prefix, "API_TARGET_STAGED")
            previous_raw = store.read(previous_key)
            if hashlib.sha256(previous_raw).hexdigest() != activation.get("report_sha256"):
                raise ValueError("staged target phase report hash drift")
            previous_report = json.loads(previous_raw.decode("utf-8"))
            phase_report, report_raw, report_sha256 = build_chained_phase_report(
                activation=activation,
                phase_evidence=phase_evidence,
                private_evidence_prefix=prefix,
                previous_report=previous_report,
            )
            store.put_create_once(current_object_key, report_raw)
        bindings = {
            "private_evidence_prefix": prefix,
            "manifest_sha256": manifest_sha256,
            "current_snapshot_hash": manifest["contract_hashes"]["pre_activation_off_snapshot"],
            "target_snapshot_hash": manifest["contract_hashes"]["target_snapshot"],
        }
        result = advance_production_activation_cas(
            database_url,
            kind=args.kind,
            source_sha=source_sha,
            expected_phase=args.expected_phase,
            target_phase=args.phase,
            evidence_sha256=report_sha256,
            bindings=bindings,
            approval=os.environ.get(args.approval_id_env, ""),
            private_object_key=current_object_key,
            evidence_chain=phase_report["evidence_chain"],
        )
        _write_create_once(
            Path(args.output),
            {
                **result,
                "manifest_sha256": manifest_sha256,
                "phase_evidence_sha256": phase_evidence["phase_evidence_sha256"],
                "report_sha256": report_sha256,
            },
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _bind_migration_parent_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bind one durable COMMERCIAL_7A migration parent")
    parser.add_argument("--kind", required=True, choices=("COMMERCIAL_7A",))
    parser.add_argument("--expected-phase", required=True, choices=("MANIFEST_SEALED",))
    parser.add_argument("--source-sha")
    parser.add_argument("--inventory-report", required=True)
    parser.add_argument("--inventory-signature", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--inventory-hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="DATA_MIGRATION_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    parser.add_argument("--job-env")
    parser.add_argument("--env-prefix", default="MIGRATION_")
    args = parser.parse_args(argv)
    try:
        source_sha = str(args.source_sha or os.environ.get("SOURCE_SHA") or "").strip().lower()
        database_url = os.environ.get(args.database_url_env, "")
        activation = read_production_activation_exact(
            database_url,
            kind=args.kind,
            source_sha=source_sha,
            phase=args.expected_phase,
        )
        inventory_path = Path(args.inventory_report)
        inventory_sha256 = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        verify_inventory_evidence(
            report_path=inventory_path,
            signature_path=Path(args.inventory_signature),
            expected_sha256=inventory_sha256,
            hmac_key=os.environ.get(args.inventory_hmac_key_env, "").encode("utf-8"),
            maximum_age_seconds=900,
        )
        manifest_path = Path(args.manifest)
        manifest_raw = manifest_path.read_bytes()
        manifest = validate_manifest(json.loads(manifest_raw.decode("utf-8")))
        if canonical_manifest_bytes(manifest) != manifest_raw:
            raise ValueError("migration parent manifest is not canonical")
        if hashlib.sha256(manifest_raw).hexdigest() != activation.get("manifest_sha256"):
            raise ValueError("migration parent manifest does not match the sealed activation")
        requested = build_migration_parent_record(
            activation,
            inventory_sha256=inventory_sha256,
            approval=os.environ.get(args.approval_id_env, ""),
            workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            workflow_attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT", "0") or 0),
        )
        result = bind_migration_parent_cas(database_url, requested=requested)
        payload = {
            **result,
            "release_activation_id": str(activation["id"]),
            "source_sha": source_sha,
            "runtime_bundle_id": activation["runtime_bundle_id"],
            "manifest_sha256": activation["manifest_sha256"],
            "inventory_sha256": inventory_sha256,
        }
        _write_create_once(Path(args.output), payload)
        if args.job_env:
            prefix = str(args.env_prefix or "").strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}_", prefix):
                raise ValueError("migration job environment prefix is invalid")
            with Path(args.job_env).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{prefix}PARENT_RUN_ID={payload['migration_parent_run_id']}\n")
                handle.write(f"{prefix}INVENTORY_SHA256={inventory_sha256}\n")
                handle.write(f"{prefix}MANIFEST_SHA256={activation['manifest_sha256']}\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


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
        "api_role": "COMMERCIAL_7A_API",
        "worker_role": "COMMERCIAL_7A_WORKER",
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
            "api_role", "worker_role", "phase", "phase_rank", "approval",
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
        "COMPLETED", "CLEANED", "PASSED", "FAILED", "DISARMED", "7A_ACCEPTED",
        "PRODUCTION_ACCEPTED"
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
    if argv[:1] == ["prepare-acceptance-fault"]:
        return _prepare_acceptance_fault_main(argv[1:])
    if argv[:1] == ["confirm-acceptance-fault"]:
        return _confirm_acceptance_fault_main(argv[1:])
    if argv[:1] == ["claim-acceptance-fault-cleanup"]:
        return _claim_acceptance_fault_cleanup_main(argv[1:])
    if argv[:1] == ["complete-acceptance-fault"]:
        return _complete_acceptance_fault_main(argv[1:])
    if argv[:1] == ["advance"]:
        return _advance_main(argv[1:])
    if argv[:1] == ["seal"]:
        return _seal_main(argv[1:])
    if argv[:1] == ["bind-migration-parent"]:
        return _bind_migration_parent_main(argv[1:])
    return _register_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
