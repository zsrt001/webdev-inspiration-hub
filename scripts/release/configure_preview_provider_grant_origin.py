#!/usr/bin/env python3
"""Lease one deterministic Preview Provider-grant alias and remove it exactly."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx


STATE_SCHEMA = "vowpic.preview-provider-grant-origin.v1"
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALIAS = re.compile(r"^vowpic-provider-[0-9a-f]{12}-[1-9][0-9]{0,19}-[1-9][0-9]{0,9}\.vercel\.app$")


def _exact_preview_origin(value: object) -> str:
    clean = str(value or "").strip().rstrip("/")
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
        raise ValueError("Preview deployment origin is invalid")
    return f"https://{parsed.hostname.lower()}"


def build_alias_host(*, source_sha: object, workflow_run_id: object, workflow_attempt: object) -> str:
    source = str(source_sha or "").strip().lower()
    run_id = str(workflow_run_id or "").strip()
    try:
        attempt = int(workflow_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError("Preview Provider alias workflow attempt is invalid") from exc
    if not _SOURCE_SHA.fullmatch(source):
        raise ValueError("Preview Provider alias source SHA is invalid")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id) or not 1 <= attempt <= 2_147_483_647:
        raise ValueError("Preview Provider alias workflow coordinates are invalid")
    return f"vowpic-provider-{source[:12]}-{run_id}-{attempt}.vercel.app"


def _validate_activation(activation: dict[str, Any]) -> None:
    expected = {
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "api_role": "PREVIEW_COMMERCIAL_API",
        "worker_role": "PREVIEW_COMMERCIAL_WORKER",
        "phase": "COMPLETED",
    }
    if not isinstance(activation, dict) or any(activation.get(key) != value for key, value in expected.items()):
        raise ValueError("Provider-grant origin requires a completed PREVIEW_COMMERCIAL activation")
    UUID(str(activation.get("activation_id") or activation.get("id") or ""))
    if not _SOURCE_SHA.fullmatch(str(activation.get("source_sha") or "")):
        raise ValueError("Provider-grant activation source SHA is invalid")
    if not _RUNTIME_ID.fullmatch(str(activation.get("runtime_bundle_id") or "")):
        raise ValueError("Provider-grant activation runtime ID is invalid")
    if not _DEPLOYMENT_ID.fullmatch(str(activation.get("api_deployment_id") or "")):
        raise ValueError("Provider-grant API deployment ID is invalid")
    if not _WORKER_ID.fullmatch(str(activation.get("worker_deployment_id") or "")):
        raise ValueError("Provider-grant Worker deployment ID is invalid")
    if not _DIGEST.fullmatch(str(activation.get("worker_image_digest") or "")):
        raise ValueError("Provider-grant Worker digest is invalid")
    _exact_preview_origin(activation.get("api_deployment_url"))


def build_origin_state(
    activation: dict[str, Any],
    *,
    alias_host: object,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_activation(activation)
    alias = str(alias_host or "").strip().lower()
    expected_alias = build_alias_host(
        source_sha=activation["source_sha"],
        workflow_run_id=activation["workflow_run_id"],
        workflow_attempt=activation["workflow_attempt"],
    )
    if alias != expected_alias or not _ALIAS.fullmatch(alias):
        raise ValueError("Provider-grant alias is not derived from the activation")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Provider-grant origin timestamp must be timezone-aware")
    return {
        "schema": STATE_SCHEMA,
        "activation_id": str(activation.get("activation_id") or activation.get("id")),
        "environment": "preview",
        "kind": "PREVIEW_COMMERCIAL",
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "api_deployment_id": activation["api_deployment_id"],
        "api_deployment_url": _exact_preview_origin(activation["api_deployment_url"]),
        "api_role": "PREVIEW_COMMERCIAL_API",
        "worker_deployment_id": activation["worker_deployment_id"],
        "worker_role": "PREVIEW_COMMERCIAL_WORKER",
        "worker_image_digest": activation["worker_image_digest"],
        "phase": "COMPLETED",
        "workflow_run_id": str(activation["workflow_run_id"]),
        "workflow_attempt": int(activation["workflow_attempt"]),
        "alias_host": alias,
        "provider_grant_origin": f"https://{alias}",
        "previous_alias_state": "ABSENT",
        "created_at": current.astimezone(timezone.utc).isoformat(),
    }


def _validate_state(state: dict[str, Any]) -> None:
    required = {
        "schema", "activation_id", "environment", "kind", "source_sha", "runtime_bundle_id",
        "api_deployment_id", "api_deployment_url", "api_role", "worker_deployment_id",
        "worker_role", "worker_image_digest", "workflow_run_id", "workflow_attempt",
        "phase", "alias_host", "provider_grant_origin", "previous_alias_state", "created_at",
    }
    if not isinstance(state, dict) or set(state) != required or state.get("schema") != STATE_SCHEMA:
        raise ValueError("Provider-grant origin state schema is invalid")
    rebuilt = build_origin_state(
        state,
        alias_host=state["alias_host"],
        now=datetime.fromisoformat(str(state["created_at"]).replace("Z", "+00:00")),
    )
    if rebuilt != state or state["previous_alias_state"] != "ABSENT":
        raise ValueError("Provider-grant origin state coordinates mismatch")


def alias_set_command(*, vercel_cli: str, state: dict[str, Any], token: str) -> list[str]:
    _validate_state(state)
    if not str(vercel_cli or "").strip() or not str(token or "").strip():
        raise ValueError("Vercel CLI and token are required")
    return [
        str(vercel_cli), "alias", "set", state["api_deployment_url"], state["alias_host"],
        "--token", str(token),
    ]


def alias_remove_command(*, vercel_cli: str, state: dict[str, Any], token: str) -> list[str]:
    _validate_state(state)
    if not str(vercel_cli or "").strip() or not str(token or "").strip():
        raise ValueError("Vercel CLI and token are required")
    return [str(vercel_cli), "alias", "rm", state["alias_host"], "--yes", "--token", str(token)]


def _version_url(state: dict[str, Any]) -> str:
    _validate_state(state)
    return f"{state['provider_grant_origin']}/api/v1/version"


def _read_alias_binding(
    state: dict[str, Any],
    *,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> dict[str, str] | None:
    """Read the exact alias from Vercel's authenticated control plane."""

    _validate_state(state)
    clean_token = str(token or "").strip()
    clean_project = str(project_id or "").strip()
    clean_team = str(team_id or "").strip()
    if not clean_token or not _DEPLOYMENT_ID.fullmatch(clean_project) or not _DEPLOYMENT_ID.fullmatch(clean_team):
        raise ValueError("Vercel alias control-plane coordinates are required")
    response = client.get(
        f"https://api.vercel.com/v4/aliases/{quote(state['alias_host'], safe='')}",
        params={"projectId": clean_project, "teamId": clean_team},
        headers={"Accept": "application/json", "Authorization": f"Bearer {clean_token}"},
        follow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200 or response.is_redirect:
        raise ValueError(f"Vercel alias control-plane read failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Vercel alias control-plane read returned invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("alias") != state["alias_host"]
        or payload.get("projectId") != clean_project
        or not _DEPLOYMENT_ID.fullmatch(str(payload.get("deploymentId") or ""))
    ):
        raise ValueError("Vercel alias control-plane binding is malformed or outside the project")
    return {
        "alias": str(payload["alias"]),
        "project_id": str(payload["projectId"]),
        "deployment_id": str(payload["deploymentId"]),
    }


def verify_alias_absent(
    state: dict[str, Any],
    *,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> bool:
    """Fail closed unless the authenticated Vercel control plane proves absence."""

    binding = _read_alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    )
    if binding is None:
        return True
    if binding["deployment_id"] != state["api_deployment_id"]:
        raise ValueError("Provider-grant alias resolves to another deployment")
    raise ValueError("Provider-grant alias already resolves to this deployment")


def verify_alias_removed(
    state: dict[str, Any],
    *,
    client: httpx.Client,
    token: str,
    project_id: str,
    team_id: str,
    attempts: int = 10,
    retry_delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if type(attempts) is not int or attempts < 1 or attempts > 30:
        raise ValueError("Provider-grant alias removal attempt count is invalid")
    delay = float(retry_delay_seconds)
    if delay < 0 or delay > 5 or attempts * delay > 60:
        raise ValueError("Provider-grant alias removal retry window is invalid")
    for attempt in range(1, attempts + 1):
        binding = _read_alias_binding(
            state,
            token=token,
            project_id=project_id,
            team_id=team_id,
            client=client,
        )
        if binding is None:
            break
        if binding["deployment_id"] != state["api_deployment_id"]:
            raise ValueError("Provider-grant alias resolves to another deployment")
        if attempt < attempts:
            sleep(delay)
            continue
        raise ValueError("Provider-grant alias still resolves to the cleaned activation")
    return {
        "schema": "vowpic.preview-provider-grant-origin-cleanup.v1",
        "state": "REMOVED",
        "alias_host": state["alias_host"],
        "activation_id": state["activation_id"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_alias_runtime(
    state: dict[str, Any],
    *,
    bypass_secret: str,
    probe_secret: str,
    client: httpx.Client,
) -> dict[str, Any]:
    if len(str(probe_secret or "")) < 32:
        raise ValueError("Provider-grant probe secret must contain at least 32 characters")
    headers = {"Accept": "application/json"}
    headers["x-vowpic-provider-probe"] = str(probe_secret)
    if bypass_secret.strip():
        headers["x-vercel-protection-bypass"] = bypass_secret.strip()
    response = client.get(_version_url(state), headers=headers, follow_redirects=False)
    if response.status_code != 200 or response.is_redirect:
        raise ValueError(f"Provider-grant alias read-back failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Provider-grant alias read-back returned invalid JSON") from exc
    expected = {
        "schema": "vowpic.runtime-bundle-report.v1",
        "source_sha": state["source_sha"],
        "runtime_bundle_id": state["runtime_bundle_id"],
        "deployment_id": state["api_deployment_id"],
        "release_role": "PREVIEW_COMMERCIAL",
        "runtime_environment": "preview",
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Provider-grant alias maps to the wrong runtime")
    return {
        "schema": "vowpic.preview-provider-grant-origin-readback.v1",
        "passed": True,
        **expected,
        "alias_host": state["alias_host"],
        "api_deployment_id": state["api_deployment_id"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider-grant origin input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--source-sha", required=True)
    plan.add_argument("--workflow-run-id", required=True)
    plan.add_argument("--workflow-attempt", required=True, type=int)
    plan.add_argument("--output", required=True)
    plan.add_argument("--job-env")
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--activation-json", required=True)
    snapshot.add_argument("--alias-host", required=True)
    snapshot.add_argument("--state-output", required=True)
    snapshot.add_argument("--output", required=True)
    for action in ("add", "remove"):
        command = subparsers.add_parser(action)
        command.add_argument("--state-file", required=True)
        command.add_argument("--vercel-cli", required=True)
        command.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
        command.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
        command.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
        command.add_argument("--bypass-secret-env", default="VERCEL_AUTOMATION_BYPASS_SECRET")
        command.add_argument("--probe-secret-env", default="PROVIDER_GRANT_PROBE_SECRET")
        command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.action == "plan":
            alias = build_alias_host(
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
            )
            result = {"alias_host": alias, "provider_grant_origin": f"https://{alias}"}
            if args.job_env:
                with Path(args.job_env).open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(f"PREVIEW_PROVIDER_ALIAS_HOST={alias}\n")
                    handle.write(f"PREVIEW_PROVIDER_GRANT_ORIGIN=https://{alias}\n")
        elif args.action == "snapshot":
            state = build_origin_state(_load_json(args.activation_json), alias_host=args.alias_host)
            _write_create_once(Path(args.state_output), state)
            result = {"state": "SNAPSHOTTED", "alias_host": state["alias_host"]}
        else:
            state = _load_json(args.state_file)
            _validate_state(state)
            token = os.environ.get(args.vercel_token_env, "").strip()
            project_id = os.environ.get(args.vercel_project_id_env, "").strip()
            team_id = os.environ.get(args.vercel_team_id_env, "").strip()
            bypass = os.environ.get(args.bypass_secret_env, "").strip()
            probe_secret = os.environ.get(args.probe_secret_env, "")
            if args.action == "add":
                command = alias_set_command(vercel_cli=args.vercel_cli, state=state, token=token)
            else:
                command = alias_remove_command(vercel_cli=args.vercel_cli, state=state, token=token)
            with httpx.Client(timeout=30.0) as client:
                if args.action == "add":
                    verify_alias_absent(
                        state,
                        token=token,
                        project_id=project_id,
                        team_id=team_id,
                        client=client,
                    )
                    subprocess.run(command, check=True, cwd=Path(__file__).resolve().parents[2])
                    result = verify_alias_runtime(
                        state,
                        bypass_secret=bypass,
                        probe_secret=probe_secret,
                        client=client,
                    )
                else:
                    binding = _read_alias_binding(
                        state,
                        token=token,
                        project_id=project_id,
                        team_id=team_id,
                        client=client,
                    )
                    alias_already_absent = binding is None
                    if binding is not None:
                        if binding["deployment_id"] != state["api_deployment_id"]:
                            raise ValueError("Provider-grant alias resolves to another deployment")
                        verify_alias_runtime(
                            state,
                            bypass_secret=bypass,
                            probe_secret=probe_secret,
                            client=client,
                        )
                        subprocess.run(command, check=True, cwd=Path(__file__).resolve().parents[2])
                    result = verify_alias_removed(
                        state,
                        token=token,
                        project_id=project_id,
                        team_id=team_id,
                        client=client,
                    )
                    result["removal_action"] = (
                        "ALREADY_ABSENT" if alias_already_absent else "REMOVED_EXACT_ALIAS"
                    )
        _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
