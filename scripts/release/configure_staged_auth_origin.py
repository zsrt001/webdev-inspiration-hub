#!/usr/bin/env python3
"""Lease and remove one exact Production staged Supabase OAuth callback."""

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
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest
from scripts.release.configure_preview_auth_origin import (
    allow_list_sha256,
    parse_uri_allow_list,
    read_uri_allow_list,
)
from scripts.release.private_evidence_store import PrivateBlobEvidenceStore


MANAGEMENT_API = "https://api.supabase.com"
STATE_SCHEMA = "vowpic.production-auth-origin-state.v1"
SHA64 = re.compile(r"^[0-9a-f]{64}$")
REMOVABLE_PHASES = (
    "WORKER_DISPATCH_ENABLED",
    "TARGET_ACCEPTED",
    "TARGET_PROMOTED",
    "PUBLIC_INVALIDATED",
    "ACTIVATED",
    "OBSERVING",
    "7A_ACCEPTED",
)


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("staged auth origin database URL is invalid")
    return clean


def exact_https_origin(value: object, *, require_vercel: bool = False) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (require_vercel and not parsed.hostname.lower().endswith(".vercel.app"))
    ):
        raise ValueError("auth origin must be one exact HTTPS origin")
    return f"https://{parsed.hostname.lower()}"


def _headers(token: str) -> dict[str, str]:
    clean = str(token or "").strip()
    if not clean:
        raise ValueError("Supabase Management API token is required")
    return {
        "Authorization": f"Bearer {clean}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "vowpic-production-auth-origin/1",
    }


def _config_url(project_ref: str) -> str:
    clean = str(project_ref or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{10,40}", clean):
        raise ValueError("Supabase project ref is invalid")
    return f"{MANAGEMENT_API}/v1/projects/{clean}/config/auth"


def patch_uri_allow_list(
    project_ref: str, value: str, *, token: str, client: httpx.Client
) -> None:
    parse_uri_allow_list(value)
    response = client.patch(
        _config_url(project_ref), headers=_headers(token), json={"uri_allow_list": value}
    )
    if response.status_code != 200:
        raise ValueError(
            f"Supabase auth config update failed with HTTP {response.status_code}"
        )


def build_state(
    activation: dict[str, Any],
    *,
    manifest_sha256: str,
    project_ref: str,
    current_allow_list: str,
) -> dict[str, Any]:
    if (
        activation.get("environment") != "production"
        or activation.get("kind") != "COMMERCIAL_7A"
        or activation.get("phase") != "WORKER_DISPATCH_ENABLED"
        or not SHA64.fullmatch(str(manifest_sha256 or ""))
        or manifest_sha256 != activation.get("manifest_sha256")
    ):
        raise ValueError("staged auth origin release coordinates are invalid")
    origin = exact_https_origin(activation.get("api_deployment_url"), require_vercel=True)
    callback = f"{origin}/pages/auth/callback"
    original_entries = parse_uri_allow_list(current_allow_list)
    if callback in original_entries:
        raise ValueError("staged callback already exists before the lease")
    target_entries = (*original_entries, callback)
    original = ",".join(original_entries)
    target = ",".join(target_entries)
    project_hash = hashlib.sha256(project_ref.strip().lower().encode("utf-8")).hexdigest()
    return {
        "schema": STATE_SCHEMA,
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "deployment_url": origin,
        "manifest_sha256": manifest_sha256,
        "project_ref_sha256": project_hash,
        "callback_url": callback,
        "original_uri_allow_list": original,
        "target_uri_allow_list": target,
        "original_sha256": allow_list_sha256(original),
        "target_sha256": allow_list_sha256(target),
    }


def validate_state(state: dict[str, Any]) -> None:
    required = {
        "schema", "activation_id", "source_sha", "runtime_bundle_id",
        "deployment_id", "deployment_url", "manifest_sha256",
        "project_ref_sha256", "callback_url", "original_uri_allow_list",
        "target_uri_allow_list", "original_sha256", "target_sha256",
    }
    if set(state) != required or state.get("schema") != STATE_SCHEMA:
        raise ValueError("staged auth origin state schema is invalid")
    origin = exact_https_origin(state["deployment_url"], require_vercel=True)
    if state["callback_url"] != f"{origin}/pages/auth/callback":
        raise ValueError("staged callback is not deployment-bound")
    original = str(state["original_uri_allow_list"])
    target = str(state["target_uri_allow_list"])
    if (
        allow_list_sha256(original) != state["original_sha256"]
        or allow_list_sha256(target) != state["target_sha256"]
        or parse_uri_allow_list(target)
        != (*parse_uri_allow_list(original), state["callback_url"])
    ):
        raise ValueError("staged auth origin snapshots are invalid")


def load_activation(database_url: str, *, source_sha: str) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
                  AND source_sha = %s
                  AND phase = ANY(%s)
                ORDER BY updated_at DESC LIMIT 2
                """,
                (source_sha, list(REMOVABLE_PHASES)),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError("exactly one staged Production activation is required")
    return rows[0]


def reserve_lease_cas(
    database_url: str,
    *,
    activation: dict[str, Any],
    state: dict[str, Any],
    object_key: str,
    approval: str,
    expires_at: datetime,
) -> dict[str, Any]:
    if expires_at.tzinfo is None:
        raise ValueError("staged auth origin lease expiry must be timezone-aware")
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-auth-origin",),
            )
            cursor.execute(
                "SELECT * FROM release_auth_origin_leases WHERE release_activation_id = %s FOR UPDATE",
                (activation["id"],),
            )
            row = cursor.fetchone()
            requested = {
                "release_activation_id": activation["id"],
                "project_ref_sha256": state["project_ref_sha256"],
                "callback_url": state["callback_url"],
                "original_sha256": state["original_sha256"],
                "target_sha256": state["target_sha256"],
                "private_object_key": object_key,
                "approval": approval,
                "expires_at": expires_at,
            }
            if row is not None:
                existing = dict(row)
                if any(str(existing.get(key)) != str(value) for key, value in requested.items()):
                    raise ValueError("existing staged auth origin lease drift")
                return existing
            cursor.execute(
                """
                INSERT INTO release_auth_origin_leases (
                    id, release_activation_id, project_ref_sha256, callback_url,
                    original_sha256, target_sha256, private_object_key, approval,
                    expires_at, state, version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'SNAPSHOTTED', 1)
                RETURNING *
                """,
                (
                    str(uuid4()),
                    activation["id"],
                    state["project_ref_sha256"],
                    state["callback_url"],
                    state["original_sha256"],
                    state["target_sha256"],
                    object_key,
                    approval,
                    expires_at,
                ),
            )
            return dict(cursor.fetchone())


def transition_lease_cas(
    database_url: str,
    *,
    activation_id: object,
    expected_states: tuple[str, ...],
    target_state: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if target_state not in {"ADDED", "REMOVED"}:
        raise ValueError("staged auth origin target state is invalid")
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("vowpic-production-auth-origin",),
            )
            cursor.execute(
                "SELECT * FROM release_auth_origin_leases WHERE release_activation_id = %s FOR UPDATE",
                (activation_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("staged auth origin lease is missing")
            row = dict(row)
            if row["state"] == target_state:
                return row
            if row["state"] not in expected_states:
                raise ValueError("staged auth origin lease transition is invalid")
            cursor.execute(
                """
                UPDATE release_auth_origin_leases
                SET state = %s,
                    removed_at = CASE WHEN %s = 'REMOVED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    version = version + 1
                WHERE id = %s AND version = %s AND state = %s
                RETURNING *
                """,
                (target_state, target_state, row["id"], row["version"], row["state"]),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise ValueError("staged auth origin lease CAS lost its fence")
            return dict(updated)


def _read_lease(database_url: str, activation_id: object) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM release_auth_origin_leases WHERE release_activation_id = %s",
                (activation_id,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise ValueError("exactly one staged auth origin lease is required")
    return rows[0]


def _read_lease_optional(
    database_url: str, activation_id: object
) -> dict[str, Any] | None:
    try:
        return _read_lease(database_url, activation_id)
    except ValueError as exc:
        if str(exc) == "exactly one staged auth origin lease is required":
            return None
        raise


def _load_manifest(path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = validate_manifest(json.loads(raw.decode("utf-8")))
    if canonical_manifest_bytes(payload) != raw:
        raise ValueError("staged auth origin manifest is not canonical")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != str(expected_sha256 or "").strip().lower():
        raise ValueError("staged auth origin manifest SHA-256 mismatch")
    return payload, digest


def validate_persisted_binding(
    state: dict[str, Any],
    *,
    activation: dict[str, Any],
    lease: dict[str, Any],
    manifest_sha256: str,
    project_ref_sha256: str,
    approval: str,
) -> None:
    validate_state(state)
    expected = {
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": activation["api_deployment_id"],
        "manifest_sha256": manifest_sha256,
        "project_ref_sha256": project_ref_sha256,
    }
    if any(str(state.get(key)) != str(value) for key, value in expected.items()):
        raise ValueError("persisted staged auth origin coordinates drifted")
    lease_expected = {
        "release_activation_id": activation["id"],
        "project_ref_sha256": project_ref_sha256,
        "callback_url": state["callback_url"],
        "original_sha256": state["original_sha256"],
        "target_sha256": state["target_sha256"],
        "approval": approval,
    }
    if any(str(lease.get(key)) != str(value) for key, value in lease_expected.items()):
        raise ValueError("persisted staged auth origin lease drifted")
    if lease.get("expires_at") is None:
        raise ValueError("persisted staged auth origin expiry is missing")


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("add", "remove", "cleanup"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--formal-base-url")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--supabase-project-ref-env", default="SUPABASE_PROJECT_REF")
    parser.add_argument("--supabase-auth-admin-token-env", default="SUPABASE_MANAGEMENT_TOKEN")
    parser.add_argument("--private-evidence-store-id-env", default="PRIVATE_EVIDENCE_STORE_ID")
    parser.add_argument("--private-evidence-token-env", default="PRIVATE_EVIDENCE_WRITE_TOKEN")
    parser.add_argument("--expires-in-seconds", type=int, default=7200)
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--require-exact-formal-callback", action="store_true")
    parser.add_argument("--require-no-wildcard", action="store_true")
    parser.add_argument("--require-no-staged-residue", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        manifest, manifest_sha = _load_manifest(
            Path(args.manifest), args.expected_manifest_sha256
        )
        if (
            manifest["release_role"] != "COMMERCIAL_7A"
            or manifest["staged_target_deployment_id"] != args.deployment_id
        ):
            raise ValueError("staged auth origin manifest deployment mismatch")
        database_url = os.environ.get(args.database_url_env, "")
        activation = load_activation(database_url, source_sha=manifest["source_sha"])
        if (
            activation.get("manifest_sha256") != manifest_sha
            or activation.get("api_deployment_id") != args.deployment_id
        ):
            raise ValueError("staged auth origin activation binding mismatch")
        project_ref = os.environ.get(args.supabase_project_ref_env, "").strip().lower()
        token = os.environ.get(args.supabase_auth_admin_token_env, "")
        _config_url(project_ref)
        approval = os.environ.get(args.approval_id_env, "").strip()
        if (
            not approval
            or len(approval) > 160
            or approval != str(activation.get("approval") or "").strip()
        ):
            raise ValueError("staged auth origin approval does not match the activation")
        if args.expires_in_seconds < 1 or args.expires_in_seconds > 86400:
            raise ValueError("staged auth origin expiry must be 1..86400 seconds")
        project_hash = hashlib.sha256(project_ref.encode("utf-8")).hexdigest()
        store = PrivateBlobEvidenceStore(
            store_id=os.environ.get(args.private_evidence_store_id_env, ""),
            token=os.environ.get(args.private_evidence_token_env, ""),
        )
        prefix = str(activation.get("private_evidence_prefix") or "").strip().strip("/\\")
        if not prefix:
            raise ValueError("staged auth origin private evidence prefix is missing")
        object_key = f"{prefix}/staged-auth-origin.json"
        with httpx.Client(timeout=20.0) as client:
            if args.action == "add":
                lease = _read_lease_optional(database_url, activation["id"])
                if lease is None:
                    current = read_uri_allow_list(project_ref, token=token, client=client)
                    state = build_state(
                        activation,
                        manifest_sha256=manifest_sha,
                        project_ref=project_ref,
                        current_allow_list=current,
                    )
                    raw = json.dumps(
                        state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8") + b"\n"
                    store.put_create_once(object_key, raw)
                    lease = reserve_lease_cas(
                        database_url,
                        activation=activation,
                        state=state,
                        object_key=object_key,
                        approval=approval,
                        expires_at=datetime.now(timezone.utc)
                        + timedelta(seconds=args.expires_in_seconds),
                    )
                else:
                    if lease["private_object_key"] != object_key:
                        raise ValueError("staged auth origin object key drifted")
                    state = json.loads(store.read(object_key).decode("utf-8"))
                validate_persisted_binding(
                    state,
                    activation=activation,
                    lease=lease,
                    manifest_sha256=manifest_sha,
                    project_ref_sha256=project_hash,
                    approval=approval,
                )
                if lease["state"] == "REMOVED":
                    raise ValueError("removed staged auth origin lease cannot be re-added")
                if lease["expires_at"] <= datetime.now(timezone.utc):
                    raise ValueError("staged auth origin lease expired before callback add")
                readback = read_uri_allow_list(project_ref, token=token, client=client)
                current_hash = allow_list_sha256(readback)
                if current_hash == state["original_sha256"]:
                    patch_uri_allow_list(
                        project_ref,
                        state["target_uri_allow_list"],
                        token=token,
                        client=client,
                    )
                    readback = read_uri_allow_list(project_ref, token=token, client=client)
                    current_hash = allow_list_sha256(readback)
                if current_hash != state["target_sha256"]:
                    raise ValueError("staged callback add read-back drift")
                transition_lease_cas(
                    database_url,
                    activation_id=activation["id"],
                    expected_states=("SNAPSHOTTED",),
                    target_state="ADDED",
                )
                report = {
                    "schema": "vowpic.production-auth-origin-report.v1",
                    "passed": True,
                    "action": "add",
                    "activation_id": str(activation["id"]),
                    "source_sha": activation["source_sha"],
                    "runtime_bundle_id": activation["runtime_bundle_id"],
                    "deployment_id": activation["api_deployment_id"],
                    "callback_sha256": hashlib.sha256(state["callback_url"].encode("utf-8")).hexdigest(),
                    "original_sha256": state["original_sha256"],
                    "target_sha256": state["target_sha256"],
                    "state": "ADDED",
                }
            else:
                lease = (
                    _read_lease_optional(database_url, activation["id"])
                    if args.action == "cleanup"
                    else _read_lease(database_url, activation["id"])
                )
                if lease is None:
                    report = {
                        "schema": "vowpic.production-auth-origin-report.v1",
                        "passed": True,
                        "action": "cleanup",
                        "activation_id": str(activation["id"]),
                        "source_sha": activation["source_sha"],
                        "runtime_bundle_id": activation["runtime_bundle_id"],
                        "deployment_id": activation["api_deployment_id"],
                        "state": "NO_LEASE",
                        "allow_list_untouched": True,
                    }
                    _write_report(Path(args.output), report)
                    return 0
                raw = store.read(lease["private_object_key"])
                state = json.loads(raw.decode("utf-8"))
                validate_persisted_binding(
                    state,
                    activation=activation,
                    lease=lease,
                    manifest_sha256=manifest_sha,
                    project_ref_sha256=project_hash,
                    approval=approval,
                )
                current = read_uri_allow_list(project_ref, token=token, client=client)
                current_hash = allow_list_sha256(current)
                if current_hash == state["target_sha256"]:
                    patch_uri_allow_list(
                        project_ref,
                        state["original_uri_allow_list"],
                        token=token,
                        client=client,
                    )
                elif current_hash != state["original_sha256"]:
                    raise ValueError("Supabase allowlist drifted after the staged callback lease")
                readback = read_uri_allow_list(project_ref, token=token, client=client)
                final_entries = parse_uri_allow_list(readback)
                if (
                    state["callback_url"] in final_entries
                    or allow_list_sha256(readback) != state["original_sha256"]
                ):
                    raise ValueError("staged callback original allowlist was not restored")
                wildcard_absent = not any(
                    any(token in entry for token in ("*", "?", "[", "]"))
                    for entry in final_entries
                )
                formal_callback_present = True
                if args.require_exact_formal_callback:
                    formal = exact_https_origin(args.formal_base_url)
                    formal_callback_present = (
                        f"{formal}/pages/auth/callback" in final_entries
                    )
                if args.require_no_wildcard and not wildcard_absent:
                    raise ValueError("Supabase allowlist contains wildcard residue")
                if args.require_exact_formal_callback and not formal_callback_present:
                    raise ValueError("formal Supabase callback is missing")
                staged_residue_absent = not any(
                    entry.endswith(".vercel.app/pages/auth/callback")
                    for entry in final_entries
                )
                if args.require_no_staged_residue and not staged_residue_absent:
                    raise ValueError("staged Supabase callback residue remains")
                transition_lease_cas(
                    database_url,
                    activation_id=activation["id"],
                    expected_states=("SNAPSHOTTED", "ADDED"),
                    target_state="REMOVED",
                )
                report = {
                    "schema": "vowpic.production-auth-origin-report.v1",
                    "passed": True,
                    "action": args.action,
                    "activation_id": str(activation["id"]),
                    "source_sha": activation["source_sha"],
                    "runtime_bundle_id": activation["runtime_bundle_id"],
                    "deployment_id": activation["api_deployment_id"],
                    "callback_sha256": hashlib.sha256(state["callback_url"].encode("utf-8")).hexdigest(),
                    "final_allow_list_sha256": allow_list_sha256(readback),
                    "wildcard_absent": wildcard_absent,
                    "formal_callback_present": formal_callback_present,
                    "staged_residue_absent": staged_residue_absent,
                    "original_sha256": state["original_sha256"],
                    "state": "REMOVED",
                }
        _write_report(Path(args.output), report)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
