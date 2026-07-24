#!/usr/bin/env python3
"""Lease one exact Supabase callback for the Preview Commercial deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release import configure_preview_auth_origin as shared  # noqa: E402


STATE_VERSION = "vowpic.preview-commercial-auth-origin.v1"


def _validate_activation(
    activation: dict[str, Any],
    *,
    allow_cleaned: bool = False,
) -> None:
    expected = {
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "api_role": "PREVIEW_COMMERCIAL_API",
    }
    for key, value in expected.items():
        if activation.get(key) != value:
            raise ValueError(f"activation {key} is not PREVIEW_COMMERCIAL")
    phases = {"COMPLETED", "CLEANED"} if allow_cleaned else {"COMPLETED"}
    if activation.get("phase") not in phases:
        raise ValueError("Preview Commercial auth origin requires a completed activation")
    UUID(str(activation.get("id") or ""))
    if not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}",
        str(activation.get("source_sha") or ""),
    ):
        raise ValueError("activation source SHA is invalid")
    if not re.fullmatch(
        r"rtb_[0-9a-f]{64}",
        str(activation.get("runtime_bundle_id") or ""),
    ):
        raise ValueError("activation runtime bundle is invalid")
    if not str(activation.get("api_deployment_id") or "").strip():
        raise ValueError("activation deployment ID is absent")
    shared.exact_https_origin(activation.get("api_deployment_url"))


def build_origin_state(
    activation: dict[str, Any],
    *,
    project_ref: str,
    uri_allow_list: object,
    callback_origin: object | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_activation(activation)
    clean_ref = project_ref.strip().lower()
    if not re.fullmatch(r"[a-z0-9]{10,40}", clean_ref):
        raise ValueError("Supabase project ref is invalid")
    original_entries = shared.parse_uri_allow_list(uri_allow_list)
    deployment_origin = shared.exact_https_origin(activation["api_deployment_url"])
    origin = shared.exact_https_origin(callback_origin or deployment_origin)
    callback = f"{origin}/pages/auth/callback"
    if callback in original_entries:
        raise ValueError("Preview Commercial callback already exists")
    target_entries = (*original_entries, callback)
    original = ",".join(original_entries)
    target = ",".join(target_entries)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("origin lease timestamp must be timezone-aware")
    return {
        "state_version": STATE_VERSION,
        "activation_id": str(activation["id"]),
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "api_deployment_id": activation["api_deployment_id"],
        "api_deployment_url": deployment_origin,
        "callback_origin": origin,
        "workflow_run_id": str(activation["workflow_run_id"]),
        "workflow_attempt": int(activation["workflow_attempt"]),
        "project_ref": clean_ref,
        "callback_url": callback,
        "original_uri_allow_list": original,
        "target_uri_allow_list": target,
        "original_sha256": shared.allow_list_sha256(original),
        "target_sha256": shared.allow_list_sha256(target),
        "created_at": current.astimezone(timezone.utc).isoformat(),
    }


def _validate_state(state: dict[str, Any]) -> None:
    required = {
        "state_version",
        "activation_id",
        "environment",
        "kind",
        "source_sha",
        "runtime_bundle_id",
        "api_deployment_id",
        "api_deployment_url",
        "callback_origin",
        "workflow_run_id",
        "workflow_attempt",
        "project_ref",
        "callback_url",
        "original_uri_allow_list",
        "target_uri_allow_list",
        "original_sha256",
        "target_sha256",
        "created_at",
    }
    if set(state) != required:
        raise ValueError("Preview Commercial auth origin state has an invalid schema")
    if (
        state["state_version"] != STATE_VERSION
        or state["environment"] != "preview"
        or state["kind"] != "PREVIEW_COMMERCIAL"
    ):
        raise ValueError("Preview Commercial auth origin state role mismatch")
    UUID(str(state["activation_id"]))
    shared.exact_https_origin(state["api_deployment_url"])
    origin = shared.exact_https_origin(state["callback_origin"])
    if state["callback_url"] != f"{origin}/pages/auth/callback":
        raise ValueError("Preview Commercial callback is not deployment-bound")
    original = str(state["original_uri_allow_list"])
    target = str(state["target_uri_allow_list"])
    if shared.allow_list_sha256(original) != state["original_sha256"]:
        raise ValueError("Preview Commercial original allowlist hash mismatch")
    if shared.allow_list_sha256(target) != state["target_sha256"]:
        raise ValueError("Preview Commercial target allowlist hash mismatch")
    original_entries = shared.parse_uri_allow_list(original)
    target_entries = shared.parse_uri_allow_list(target)
    if target_entries != (*original_entries, state["callback_url"]):
        raise ValueError("Preview Commercial target is not one exact callback addition")


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
                       workflow_run_id, workflow_attempt, phase
                FROM release_activations WHERE id = %s
                """,
                (activation_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError("Preview Commercial activation does not exist")
    return dict(row)


def _same_activation(
    activation: dict[str, Any],
    state: dict[str, Any],
    *,
    allow_cleaned: bool = False,
) -> None:
    _validate_activation(activation, allow_cleaned=allow_cleaned)
    for key in (
        "source_sha",
        "runtime_bundle_id",
        "api_deployment_id",
        "api_deployment_url",
        "workflow_run_id",
        "workflow_attempt",
    ):
        if str(activation[key]) != str(state[key]):
            raise ValueError(f"auth origin activation {key} mismatch")


def _read_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Preview Commercial auth origin state must be an object")
    _validate_state(payload)
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _add(
    state: dict[str, Any],
    *,
    token: str,
    client: httpx.Client,
) -> dict[str, Any]:
    project_ref = str(state["project_ref"])
    current = shared.read_uri_allow_list(project_ref, token=token, client=client)
    if shared.allow_list_sha256(current) != state["original_sha256"]:
        raise ValueError("Supabase allowlist changed before Commercial callback add")
    desired = str(state["target_uri_allow_list"])
    shared._patch_uri_allow_list(  # noqa: SLF001
        project_ref,
        desired,
        token=token,
        client=client,
    )
    readback = shared.read_uri_allow_list(project_ref, token=token, client=client)
    if shared.allow_list_sha256(readback) != state["target_sha256"]:
        raise ValueError("Supabase Commercial callback read-back mismatch")
    return {"state": "ADDED", "sha256": state["target_sha256"]}


def _remove(
    state: dict[str, Any],
    *,
    token: str,
    client: httpx.Client,
) -> dict[str, Any]:
    return shared.remove_owned_callback(
        project_ref=str(state["project_ref"]),
        callback_url=str(state["callback_url"]),
        original_sha256=str(state["original_sha256"]),
        target_sha256=str(state["target_sha256"]),
        token=token,
        client=client,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--activation-id", required=True)
    snapshot.add_argument("--source-sha", required=True)
    snapshot.add_argument("--workflow-run-id", required=True)
    snapshot.add_argument("--workflow-attempt", required=True, type=int)
    snapshot.add_argument("--callback-origin")
    snapshot.add_argument("--state-output", required=True)
    for name in ("add", "remove"):
        subparsers.add_parser(name).add_argument("--state-file", required=True)
    for command in (
        snapshot,
        subparsers.choices["add"],
        subparsers.choices["remove"],
    ):
        command.add_argument(
            "--database-url-env",
            default="PREVIEW_CONTROL_DATABASE_URL",
        )
        command.add_argument(
            "--management-token-env",
            default="SUPABASE_MANAGEMENT_TOKEN",
        )
        command.add_argument(
            "--project-ref-env",
            default="SUPABASE_PROJECT_REF",
        )
        command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "").strip()
        token = os.environ.get(args.management_token_env, "").strip()
        project_ref = os.environ.get(args.project_ref_env, "").strip().lower()
        if not database_url or not token or not project_ref:
            raise ValueError(
                "database URL, Management API token, and Supabase project ref are required"
            )
        with httpx.Client(timeout=20.0) as client:
            if args.action == "snapshot":
                activation = load_activation(database_url, args.activation_id)
                _validate_activation(activation)
                if (
                    activation["source_sha"] != args.source_sha
                    or str(activation["workflow_run_id"]) != args.workflow_run_id
                    or int(activation["workflow_attempt"]) != args.workflow_attempt
                ):
                    raise ValueError(
                        "Preview Commercial activation workflow coordinates mismatch"
                    )
                current = shared.read_uri_allow_list(
                    project_ref,
                    token=token,
                    client=client,
                )
                state = build_origin_state(
                    activation,
                    project_ref=project_ref,
                    uri_allow_list=current,
                    callback_origin=args.callback_origin,
                )
                _write_create_once(Path(args.state_output), state)
                result = {
                    "state": "SNAPSHOT_RECORDED",
                    "activation_id": args.activation_id,
                }
            else:
                state = _read_state(Path(args.state_file))
                if state["project_ref"] != project_ref:
                    raise ValueError("Supabase project ref does not match auth state")
                activation = load_activation(database_url, str(state["activation_id"]))
                _same_activation(
                    activation,
                    state,
                    allow_cleaned=args.action == "remove",
                )
                result = (
                    _add(state, token=token, client=client)
                    if args.action == "add"
                    else _remove(state, token=token, client=client)
                )
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
