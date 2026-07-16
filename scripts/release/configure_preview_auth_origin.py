#!/usr/bin/env python3
"""Lease one exact Supabase Preview callback and restore it with hash-based CAS."""

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
from uuid import UUID

import httpx


MANAGEMENT_API = "https://api.supabase.com"
STATE_VERSION = "vowpic.preview-auth-origin.v1"


def parse_uri_allow_list(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, str):
        raise ValueError("Supabase uri_allow_list must be a string")
    entries = tuple(item.strip() for item in value.split(","))
    if any(not item for item in entries):
        raise ValueError("Supabase uri_allow_list contains an empty entry")
    if len(set(entries)) != len(entries):
        raise ValueError("Supabase uri_allow_list contains duplicate entries")
    for entry in entries:
        if any(token in entry for token in ("*", "?", "[", "]")):
            raise ValueError("wildcard Supabase redirect URLs are forbidden")
        parsed = urlsplit(entry)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(char.isspace() for char in entry)
        ):
            raise ValueError("Supabase redirect URLs must be exact HTTP(S) URLs")
    return entries


def _serialize(entries: tuple[str, ...] | list[str]) -> str:
    return ",".join(entries)


def allow_list_sha256(value: object) -> str:
    canonical = json.dumps(
        sorted(parse_uri_allow_list(value)),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def exact_https_origin(value: object) -> str:
    clean = str(value or "").strip()
    if any(token in clean for token in ("*", "?", "[", "]")):
        raise ValueError("Preview origin cannot contain wildcard syntax")
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
        raise ValueError("Preview origin must be one exact HTTPS Vercel deployment origin")
    return f"https://{parsed.hostname.lower()}"


def _validate_activation(activation: dict[str, Any], *, allow_cleaned: bool = False) -> None:
    expected = {
        "environment": "preview",
        "kind": "PREVIEW_IDENTITY",
        "api_role": "PREVIEW_IDENTITY",
    }
    for key, value in expected.items():
        if activation.get(key) != value:
            raise ValueError(f"activation {key} is not PREVIEW_IDENTITY")
    allowed_phases = {"COMPLETED", "CLEANED"} if allow_cleaned else {"COMPLETED"}
    if activation.get("phase") not in allowed_phases:
        raise ValueError("Preview auth origin requires a completed activation")
    UUID(str(activation.get("id") or ""))
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(activation.get("source_sha") or "")):
        raise ValueError("activation source SHA is invalid")
    if not re.fullmatch(r"rtb_[0-9a-f]{64}", str(activation.get("runtime_bundle_id") or "")):
        raise ValueError("activation runtime bundle is invalid")
    if not str(activation.get("api_deployment_id") or "").strip():
        raise ValueError("activation deployment ID is absent")
    exact_https_origin(activation.get("api_deployment_url"))


def build_origin_state(
    activation: dict[str, Any],
    *,
    project_ref: str,
    uri_allow_list: object,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_activation(activation)
    clean_ref = project_ref.strip().lower()
    if not re.fullmatch(r"[a-z0-9]{10,40}", clean_ref):
        raise ValueError("Supabase project ref is invalid")
    original_entries = parse_uri_allow_list(uri_allow_list)
    origin = exact_https_origin(activation["api_deployment_url"])
    callback = f"{origin}/pages/auth/callback"
    if callback in original_entries:
        raise ValueError("Preview callback already exists and cannot be leased safely")
    target_entries = (*original_entries, callback)
    original = _serialize(original_entries)
    target = _serialize(target_entries)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("origin lease timestamp must be timezone-aware")
    return {
        "state_version": STATE_VERSION,
        "activation_id": str(activation["id"]),
        "environment": "preview",
        "kind": "PREVIEW_IDENTITY",
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "api_deployment_id": activation["api_deployment_id"],
        "api_deployment_url": origin,
        "workflow_run_id": str(activation["workflow_run_id"]),
        "workflow_attempt": int(activation["workflow_attempt"]),
        "project_ref": clean_ref,
        "callback_url": callback,
        "original_uri_allow_list": original,
        "target_uri_allow_list": target,
        "original_sha256": allow_list_sha256(original),
        "target_sha256": allow_list_sha256(target),
        "created_at": current.astimezone(timezone.utc).isoformat(),
    }


def _validate_state(state: dict[str, Any]) -> None:
    required = {
        "state_version", "activation_id", "environment", "kind", "source_sha",
        "runtime_bundle_id", "api_deployment_id", "api_deployment_url",
        "workflow_run_id", "workflow_attempt", "project_ref", "callback_url",
        "original_uri_allow_list", "target_uri_allow_list", "original_sha256",
        "target_sha256", "created_at",
    }
    if set(state) != required:
        raise ValueError("Preview auth origin state has an invalid schema")
    if state["state_version"] != STATE_VERSION:
        raise ValueError("Preview auth origin state version mismatch")
    if state["environment"] != "preview" or state["kind"] != "PREVIEW_IDENTITY":
        raise ValueError("Preview auth origin state role mismatch")
    UUID(str(state["activation_id"]))
    origin = exact_https_origin(state["api_deployment_url"])
    if state["callback_url"] != f"{origin}/pages/auth/callback":
        raise ValueError("Preview callback is not derived from the deployment origin")
    original = str(state["original_uri_allow_list"])
    target = str(state["target_uri_allow_list"])
    if allow_list_sha256(original) != state["original_sha256"]:
        raise ValueError("Preview auth origin original snapshot hash mismatch")
    if allow_list_sha256(target) != state["target_sha256"]:
        raise ValueError("Preview auth origin target snapshot hash mismatch")
    original_entries = parse_uri_allow_list(original)
    target_entries = parse_uri_allow_list(target)
    if target_entries != (*original_entries, state["callback_url"]):
        raise ValueError("Preview auth origin target is not one exact callback addition")


def decide_restore_action(state: dict[str, Any], current_uri_allow_list: object) -> str:
    _validate_state(state)
    current_hash = allow_list_sha256(current_uri_allow_list)
    if current_hash == state["original_sha256"]:
        return "ALREADY_RESTORED"
    if current_hash == state["target_sha256"]:
        return "RESTORE"
    raise ValueError("Supabase redirect allowlist drifted after the Preview lease")


def _headers(token: str) -> dict[str, str]:
    clean = token.strip()
    if not clean:
        raise ValueError("Supabase Management API token is required")
    return {
        "Authorization": f"Bearer {clean}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "vowpic-preview-auth-origin/1",
    }


def _config_url(project_ref: str) -> str:
    if not re.fullmatch(r"[a-z0-9]{10,40}", project_ref):
        raise ValueError("Supabase project ref is invalid")
    return f"{MANAGEMENT_API}/v1/projects/{project_ref}/config/auth"


def read_uri_allow_list(
    project_ref: str,
    *,
    token: str,
    client: httpx.Client,
) -> str:
    response = client.get(_config_url(project_ref), headers=_headers(token))
    if response.status_code != 200:
        raise ValueError(f"Supabase auth config read failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Supabase auth config returned invalid JSON") from exc
    value = payload.get("uri_allow_list") if isinstance(payload, dict) else None
    if not isinstance(value, str):
        raise ValueError("Supabase auth config omitted uri_allow_list")
    parse_uri_allow_list(value)
    return value


def _patch_uri_allow_list(
    project_ref: str,
    value: str,
    *,
    token: str,
    client: httpx.Client,
) -> None:
    parse_uri_allow_list(value)
    response = client.patch(
        _config_url(project_ref),
        headers=_headers(token),
        json={"uri_allow_list": value},
    )
    if response.status_code != 200:
        raise ValueError(f"Supabase auth config update failed with HTTP {response.status_code}")


def apply_origin_state(
    state: dict[str, Any],
    *,
    action: str,
    token: str,
    client: httpx.Client,
) -> dict[str, str]:
    _validate_state(state)
    project_ref = str(state["project_ref"])
    current = read_uri_allow_list(project_ref, token=token, client=client)
    if action == "add":
        if allow_list_sha256(current) != state["original_sha256"]:
            raise ValueError("Supabase redirect allowlist changed before the Preview callback add")
        desired = str(state["target_uri_allow_list"])
        result = "ADDED"
    elif action == "remove":
        decision = decide_restore_action(state, current)
        if decision == "ALREADY_RESTORED":
            return {"state": decision, "sha256": state["original_sha256"]}
        desired = str(state["original_uri_allow_list"])
        result = "RESTORED"
    else:
        raise ValueError("origin action must be add or remove")
    _patch_uri_allow_list(project_ref, desired, token=token, client=client)
    readback = read_uri_allow_list(project_ref, token=token, client=client)
    expected_hash = allow_list_sha256(desired)
    if allow_list_sha256(readback) != expected_hash:
        raise ValueError("Supabase redirect allowlist read-back mismatch")
    return {"state": result, "sha256": expected_hash}


def remove_owned_callback(
    *,
    project_ref: str,
    callback_url: str,
    original_sha256: str,
    target_sha256: str,
    token: str,
    client: httpx.Client,
) -> dict[str, str | bool]:
    """Remove only this activation's exact callback, preserving unrelated changes."""
    clean_callback = str(callback_url).strip()
    parsed = urlsplit(clean_callback)
    origin = exact_https_origin(f"{parsed.scheme}://{parsed.netloc}")
    if clean_callback != f"{origin}/pages/auth/callback":
        raise ValueError("Preview cleanup callback is not an exact deployment callback")
    for label, value in (
        ("original", original_sha256),
        ("target", target_sha256),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            raise ValueError(f"Preview cleanup {label} snapshot hash is invalid")
    if original_sha256 == target_sha256:
        raise ValueError("Preview cleanup snapshot hashes must differ")

    current = read_uri_allow_list(project_ref, token=token, client=client)
    current_entries = parse_uri_allow_list(current)
    current_hash = allow_list_sha256(current)
    if clean_callback not in current_entries:
        exact = current_hash == original_sha256
        return {
            "state": "ALREADY_RESTORED" if exact else "ALREADY_REMOVED_WITH_DRIFT",
            "sha256": current_hash,
            "exact_original_restored": exact,
            "concurrent_changes_preserved": not exact,
        }
    if current_hash == original_sha256:
        raise ValueError("Preview callback is present in the original snapshot hash")

    desired_entries = tuple(entry for entry in current_entries if entry != clean_callback)
    desired = _serialize(desired_entries)
    _patch_uri_allow_list(project_ref, desired, token=token, client=client)
    readback = read_uri_allow_list(project_ref, token=token, client=client)
    if clean_callback in parse_uri_allow_list(readback):
        raise ValueError("Preview callback remains after cleanup read-back")
    expected_hash = allow_list_sha256(desired)
    if allow_list_sha256(readback) != expected_hash:
        raise ValueError("Supabase redirect allowlist cleanup read-back mismatch")
    exact = expected_hash == original_sha256
    return {
        "state": "RESTORED" if exact else "OWNED_CALLBACK_REMOVED",
        "sha256": expected_hash,
        "exact_original_restored": exact,
        "concurrent_changes_preserved": current_hash != target_sha256,
    }


def _database_url(value: str) -> str:
    clean = value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    if not clean:
        raise ValueError("Preview control-plane database URL is required")
    return clean


def load_activation(database_url: str, activation_id: str) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    UUID(activation_id)
    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, environment, kind, source_sha, runtime_bundle_id,
                       api_deployment_id, api_deployment_url, api_role,
                       workflow_run_id, workflow_attempt, phase, phase_rank, version,
                       current_snapshot_hash, target_snapshot_hash
                FROM release_activations WHERE id = %s
                """,
                (activation_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError("Preview activation does not exist")
    return dict(row)


def record_snapshot_hashes(database_url: str, state: dict[str, Any]) -> None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    _validate_state(state)
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM release_activations WHERE id = %s FOR UPDATE",
                (state["activation_id"],),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Preview activation does not exist")
            activation = dict(row)
            _validate_activation(activation)
            for key in (
                "source_sha", "runtime_bundle_id", "api_deployment_id",
                "api_deployment_url", "workflow_run_id", "workflow_attempt",
            ):
                if str(activation[key]) != str(state[key]):
                    raise ValueError(f"origin snapshot activation {key} mismatch")
            current_hash = activation.get("current_snapshot_hash")
            target_hash = activation.get("target_snapshot_hash")
            expected = (state["original_sha256"], state["target_sha256"])
            if (current_hash, target_hash) == expected:
                return
            if current_hash is not None or target_hash is not None:
                raise ValueError("Preview activation already owns a different snapshot")
            cursor.execute(
                """
                UPDATE release_activations
                SET current_snapshot_hash = %s, target_snapshot_hash = %s,
                    version = version + 1
                WHERE id = %s AND version = %s AND phase = 'COMPLETED'
                """,
                (*expected, state["activation_id"], activation["version"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Preview activation snapshot CAS failed")


def _assert_activation_state(database_url: str, state: dict[str, Any]) -> None:
    activation = load_activation(database_url, str(state["activation_id"]))
    _validate_activation(activation, allow_cleaned=True)
    for key in (
        "source_sha", "runtime_bundle_id", "api_deployment_id",
        "api_deployment_url", "workflow_run_id", "workflow_attempt",
    ):
        if str(activation[key]) != str(state[key]):
            raise ValueError(f"origin state activation {key} mismatch")
    if (
        activation.get("current_snapshot_hash") != state["original_sha256"]
        or activation.get("target_snapshot_hash") != state["target_sha256"]
    ):
        raise ValueError("origin state hashes are not registered on the activation")


def _read_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Preview auth origin state must be a JSON object")
    _validate_state(payload)
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--activation-id", required=True)
    snapshot.add_argument("--source-sha", required=True)
    snapshot.add_argument("--workflow-run-id", required=True)
    snapshot.add_argument("--workflow-attempt", required=True, type=int)
    snapshot.add_argument("--state-output", required=True)
    for action in ("add", "remove"):
        command = subparsers.add_parser(action)
        command.add_argument("--state-file", required=True)
    for command in (snapshot, *[subparsers.choices[name] for name in ("add", "remove")]):
        command.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
        command.add_argument("--management-token-env", default="SUPABASE_MANAGEMENT_TOKEN")
        command.add_argument("--project-ref-env", default="SUPABASE_PROJECT_REF")
        command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "").strip()
        token = os.environ.get(args.management_token_env, "").strip()
        project_ref = os.environ.get(args.project_ref_env, "").strip().lower()
        if not database_url or not token or not project_ref:
            raise ValueError("database URL, Management API token, and Supabase project ref are required")
        with httpx.Client(timeout=20.0) as client:
            if args.action == "snapshot":
                activation = load_activation(database_url, args.activation_id)
                _validate_activation(activation)
                if (
                    activation["source_sha"] != args.source_sha
                    or str(activation["workflow_run_id"]) != args.workflow_run_id
                    or int(activation["workflow_attempt"]) != args.workflow_attempt
                ):
                    raise ValueError("Preview activation workflow coordinates mismatch")
                current = read_uri_allow_list(project_ref, token=token, client=client)
                state = build_origin_state(
                    activation,
                    project_ref=project_ref,
                    uri_allow_list=current,
                )
                record_snapshot_hashes(database_url, state)
                _write_create_once(Path(args.state_output), state)
                result = {"state": "SNAPSHOT_RECORDED", "activation_id": args.activation_id}
            else:
                state = _read_state(Path(args.state_file))
                if state["project_ref"] != project_ref:
                    raise ValueError("Supabase project ref does not match the origin state")
                _assert_activation_state(database_url, state)
                result = apply_origin_state(
                    state,
                    action=args.action,
                    token=token,
                    client=client,
                )
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
