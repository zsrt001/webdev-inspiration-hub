#!/usr/bin/env python3
"""Lease one fixed Creem Test callback alias to one exact Preview deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit
import re
import subprocess
import sys
import time
from typing import Any, Callable
from uuid import UUID

import httpx


STATE_VERSION = "vowpic.preview-commercial-callback.v1"
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_CALLBACK_HOST = re.compile(
    r"^vowpic-creem-[a-z0-9](?:[a-z0-9-]{0,52}[a-z0-9])?\.vercel\.app$"
)


def _preview_origin(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.lower().endswith(".vercel.app")
    ):
        raise ValueError("Preview deployment origin is invalid")
    return f"https://{parsed.hostname.lower()}"


def _validate_activation(activation: dict[str, Any]) -> None:
    if (
        activation.get("report_version")
        != "vowpic.preview-commercial-activation.v1"
        or activation.get("passed") is not True
        or activation.get("kind") != "PREVIEW_COMMERCIAL"
        or activation.get("phase") != "COMPLETED"
    ):
        raise ValueError("Creem callback requires one completed Preview Commercial activation")
    UUID(str(activation.get("activation_id") or ""))
    if not _SOURCE_SHA.fullmatch(str(activation.get("source_sha") or "")):
        raise ValueError("Preview Commercial source SHA is invalid")
    if not _RUNTIME_ID.fullmatch(str(activation.get("runtime_bundle_id") or "")):
        raise ValueError("Preview Commercial runtime bundle ID is invalid")
    if not _DEPLOYMENT_ID.fullmatch(str(activation.get("api_deployment_id") or "")):
        raise ValueError("Preview Commercial deployment ID is invalid")
    _preview_origin(activation.get("api_deployment_url"))


def build_callback_state(
    activation: dict[str, Any],
    *,
    callback_host: object,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_activation(activation)
    host = str(callback_host or "").strip().lower()
    if not _CALLBACK_HOST.fullmatch(host):
        raise ValueError(
            "Preview Creem callback host must be a dedicated vowpic-creem-*.vercel.app alias"
        )
    deployment_origin = _preview_origin(activation["api_deployment_url"])
    if urlsplit(deployment_origin).hostname == host:
        raise ValueError("Preview Creem callback must not be the deployment hostname")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Preview Creem callback timestamp must be timezone-aware")
    return {
        "state_version": STATE_VERSION,
        "activation_id": str(activation["activation_id"]),
        "source_sha": str(activation["source_sha"]),
        "runtime_bundle_id": str(activation["runtime_bundle_id"]),
        "api_deployment_id": str(activation["api_deployment_id"]),
        "api_deployment_url": deployment_origin,
        "workflow_run_id": str(activation["workflow_run_id"]),
        "workflow_attempt": int(activation["workflow_attempt"]),
        "callback_host": host,
        "callback_origin": f"https://{host}",
        "webhook_url": f"https://{host}/api/v1/payments/webhook/creem",
        "previous_alias_state": "ABSENT",
        "created_at": current.astimezone(timezone.utc).isoformat(),
    }


def _validate_state(state: dict[str, Any]) -> None:
    required = {
        "state_version",
        "activation_id",
        "source_sha",
        "runtime_bundle_id",
        "api_deployment_id",
        "api_deployment_url",
        "workflow_run_id",
        "workflow_attempt",
        "callback_host",
        "callback_origin",
        "webhook_url",
        "previous_alias_state",
        "created_at",
    }
    if set(state) != required or state.get("state_version") != STATE_VERSION:
        raise ValueError("Preview Creem callback state schema is invalid")
    activation = {
        "report_version": "vowpic.preview-commercial-activation.v1",
        "passed": True,
        "activation_id": state["activation_id"],
        "kind": "PREVIEW_COMMERCIAL",
        "phase": "COMPLETED",
        "source_sha": state["source_sha"],
        "runtime_bundle_id": state["runtime_bundle_id"],
        "api_deployment_id": state["api_deployment_id"],
        "api_deployment_url": state["api_deployment_url"],
        "workflow_run_id": state["workflow_run_id"],
        "workflow_attempt": state["workflow_attempt"],
    }
    rebuilt = build_callback_state(
        activation,
        callback_host=state["callback_host"],
        now=datetime.fromisoformat(str(state["created_at"])),
    )
    if rebuilt != state or state["previous_alias_state"] != "ABSENT":
        raise ValueError("Preview Creem callback state coordinates mismatch")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _alias_binding(
    state: dict[str, Any],
    *,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> dict[str, str] | None:
    _validate_state(state)
    if not token or not _DEPLOYMENT_ID.fullmatch(project_id) or not _DEPLOYMENT_ID.fullmatch(team_id):
        raise ValueError("Vercel alias control-plane coordinates are required")
    response = client.get(
        f"https://api.vercel.com/v4/aliases/{quote(state['callback_host'], safe='')}",
        params={"projectId": project_id, "teamId": team_id},
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200 or response.is_redirect:
        raise ValueError(
            f"Vercel callback alias read failed with HTTP {response.status_code}"
        )
    payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("alias") != state["callback_host"]
        or payload.get("projectId") != project_id
        or not _DEPLOYMENT_ID.fullmatch(str(payload.get("deploymentId") or ""))
    ):
        raise ValueError("Vercel callback alias binding is malformed or outside the project")
    return {
        "alias": str(payload["alias"]),
        "project_id": str(payload["projectId"]),
        "deployment_id": str(payload["deploymentId"]),
    }


def _run_alias(command: list[str]) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ValueError("Vercel callback alias command failed")


def add_callback_alias(
    state: dict[str, Any],
    *,
    vercel_cli: str,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> dict[str, Any]:
    _validate_state(state)
    if _alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    ) is not None:
        raise ValueError("Preview Creem callback alias is not absent before binding")
    _run_alias(
        [
            vercel_cli,
            "alias",
            "set",
            state["api_deployment_url"],
            state["callback_host"],
            "--token",
            token,
        ]
    )
    binding = _alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    )
    if binding is None or binding["deployment_id"] != state["api_deployment_id"]:
        raise ValueError("Preview Creem callback alias read-back mismatch")
    return {
        "schema": "vowpic.preview-commercial-callback-binding.v1",
        "state": "ADDED",
        "activation_id": state["activation_id"],
        "api_deployment_id": state["api_deployment_id"],
        "callback_host": state["callback_host"],
        "webhook_url": state["webhook_url"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def remove_callback_alias(
    state: dict[str, Any],
    *,
    vercel_cli: str,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
    attempts: int = 10,
    retry_delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    _validate_state(state)
    binding = _alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    )
    if binding is not None:
        if binding["deployment_id"] != state["api_deployment_id"]:
            raise ValueError("Preview Creem callback alias points to another deployment")
        _run_alias(
            [
                vercel_cli,
                "alias",
                "rm",
                state["callback_host"],
                "--yes",
                "--token",
                token,
            ]
        )
    if not 1 <= attempts <= 30 or not 0 <= retry_delay_seconds <= 5:
        raise ValueError("Preview Creem callback cleanup retry window is invalid")
    for attempt in range(1, attempts + 1):
        remaining = _alias_binding(
            state,
            token=token,
            project_id=project_id,
            team_id=team_id,
            client=client,
        )
        if remaining is None:
            break
        if remaining["deployment_id"] != state["api_deployment_id"]:
            raise ValueError("Preview Creem callback alias changed during cleanup")
        if attempt == attempts:
            raise ValueError("Preview Creem callback alias was not removed")
        sleep(retry_delay_seconds)
    return {
        "schema": "vowpic.preview-commercial-callback-cleanup.v1",
        "state": "REMOVED",
        "activation_id": state["activation_id"],
        "callback_host": state["callback_host"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--activation-json", required=True)
    snapshot.add_argument("--callback-host", required=True)
    snapshot.add_argument("--state-output", required=True)
    snapshot.add_argument("--output", required=True)
    for name in ("add", "remove"):
        command = subparsers.add_parser(name)
        command.add_argument("--state-file", required=True)
        command.add_argument("--vercel-cli", required=True)
        command.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
        command.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
        command.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
        command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.action == "snapshot":
            state = build_callback_state(
                _load(Path(args.activation_json)),
                callback_host=args.callback_host,
            )
            _write_create_once(Path(args.state_output), state)
            result = {
                "state": "SNAPSHOTTED",
                "activation_id": state["activation_id"],
                "callback_host": state["callback_host"],
            }
        else:
            state = _load(Path(args.state_file))
            _validate_state(state)
            token = os.environ.get(args.vercel_token_env, "").strip()
            project_id = os.environ.get(args.vercel_project_id_env, "").strip()
            team_id = os.environ.get(args.vercel_team_id_env, "").strip()
            with httpx.Client(timeout=30.0) as client:
                result = (
                    add_callback_alias(
                        state,
                        vercel_cli=args.vercel_cli,
                        token=token,
                        project_id=project_id,
                        team_id=team_id,
                        client=client,
                    )
                    if args.action == "add"
                    else remove_callback_alias(
                        state,
                        vercel_cli=args.vercel_cli,
                        token=token,
                        project_id=project_id,
                        team_id=team_id,
                        client=client,
                    )
                )
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
        httpx.HTTPError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
