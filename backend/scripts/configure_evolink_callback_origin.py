#!/usr/bin/env python3
"""Bind one immutable release deployment to its version-specific EvoLink callback alias."""

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

import httpx


_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_ALIAS = re.compile(
    r"^vowpic-evolink-[0-9a-f]{12}-[1-9][0-9]{0,19}-"
    r"[1-9][0-9]{0,9}\.vercel\.app$"
)


def build_callback_host(
    *,
    source_sha: object,
    workflow_run_id: object,
    workflow_attempt: object,
) -> str:
    source = str(source_sha or "").strip().lower()
    run_id = str(workflow_run_id or "").strip()
    try:
        attempt = int(workflow_attempt)
    except (TypeError, ValueError) as exc:
        raise ValueError("EvoLink callback workflow attempt is invalid") from exc
    if not _SOURCE_SHA.fullmatch(source):
        raise ValueError("EvoLink callback source SHA is invalid")
    if not _RUN_ID.fullmatch(run_id) or not 1 <= attempt <= 2_147_483_647:
        raise ValueError("EvoLink callback workflow coordinates are invalid")
    return f"vowpic-evolink-{source[:12]}-{run_id}-{attempt}.vercel.app"


def _exact_origin(value: object) -> str:
    parsed = urlsplit(str(value or "").strip().rstrip("/"))
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
        raise ValueError("EvoLink callback deployment URL is invalid")
    return f"https://{parsed.hostname.lower()}"


def build_state(
    *,
    source_sha: object,
    workflow_run_id: object,
    workflow_attempt: object,
    deployment_url: object,
    deployment_id: object,
    runtime_bundle_id: object,
) -> dict[str, Any]:
    source = str(source_sha or "").strip().lower()
    run_id = str(workflow_run_id or "").strip()
    attempt = int(workflow_attempt)
    host = build_callback_host(
        source_sha=source,
        workflow_run_id=run_id,
        workflow_attempt=attempt,
    )
    deployment = str(deployment_id or "").strip()
    runtime = str(runtime_bundle_id or "").strip().lower()
    if not _DEPLOYMENT_ID.fullmatch(deployment):
        raise ValueError("EvoLink callback deployment ID is invalid")
    if not _RUNTIME_ID.fullmatch(runtime):
        raise ValueError("EvoLink callback runtime bundle ID is invalid")
    return {
        "schema": "vowpic.evolink-callback-origin.v1",
        "source_sha": source,
        "workflow_run_id": run_id,
        "workflow_attempt": attempt,
        "deployment_url": _exact_origin(deployment_url),
        "deployment_id": deployment,
        "runtime_bundle_id": runtime,
        "callback_host": host,
        "callback_origin": f"https://{host}",
    }


def _read_alias_binding(
    state: dict[str, Any],
    *,
    token: str,
    project_id: str,
    team_id: str,
    client: httpx.Client,
) -> dict[str, str] | None:
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
            f"Vercel EvoLink callback alias read failed with HTTP {response.status_code}"
        )
    payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("alias") != state["callback_host"]
        or payload.get("projectId") != project_id
        or not _DEPLOYMENT_ID.fullmatch(str(payload.get("deploymentId") or ""))
    ):
        raise ValueError("Vercel EvoLink callback alias binding is malformed")
    return {
        "alias": str(payload["alias"]),
        "project_id": str(payload["projectId"]),
        "deployment_id": str(payload["deploymentId"]),
    }


def _verify_runtime(
    state: dict[str, Any],
    *,
    bypass_secret: str,
    probe_secret: str,
    client: httpx.Client,
    attempts: int = 10,
    delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bypass_secret.strip():
        headers["x-vercel-protection-bypass"] = bypass_secret.strip()
    if len(probe_secret.strip()) < 32:
        raise ValueError("EvoLink callback runtime probe secret is required")
    headers["x-vowpic-provider-probe"] = probe_secret.strip()
    last_observation: dict[str, Any] = {"state": "not_attempted"}
    for attempt in range(1, attempts + 1):
        response = client.get(
            f"{state['callback_origin']}/api/v1/version",
            headers=headers,
            follow_redirects=False,
        )
        last_observation = {
            "attempt": attempt,
            "status_code": response.status_code,
            "redirect": response.is_redirect,
        }
        if response.status_code == 200 and not response.is_redirect:
            try:
                payload = response.json()
            except ValueError:
                last_observation["payload_state"] = "invalid_json"
                payload = None
            expected = {
                "schema": "vowpic.runtime-bundle-report.v1",
                "source_sha": state["source_sha"],
                "runtime_bundle_id": state["runtime_bundle_id"],
                "deployment_id": state["deployment_id"],
            }
            if isinstance(payload, dict) and all(
                payload.get(key) == value for key, value in expected.items()
            ):
                return {
                    "schema": "vowpic.evolink-callback-origin-binding.v1",
                    "passed": True,
                    **state,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            if payload is not None:
                last_observation["payload_state"] = (
                    "coordinate_mismatch" if isinstance(payload, dict) else "not_an_object"
                )
                if isinstance(payload, dict):
                    last_observation["mismatched_fields"] = sorted(
                        key for key, value in expected.items() if payload.get(key) != value
                    )
        if attempt < attempts:
            sleep(delay_seconds)
    raise ValueError(
        "EvoLink callback alias did not resolve to the exact deployment; "
        f"last_observation={json.dumps(last_observation, sort_keys=True)}"
    )


def bind_alias(
    state: dict[str, Any],
    *,
    vercel_cli: str,
    token: str,
    project_id: str,
    team_id: str,
    bypass_secret: str,
    probe_secret: str,
    client: httpx.Client,
) -> dict[str, Any]:
    if (
        not vercel_cli.strip()
        or not token.strip()
        or not _DEPLOYMENT_ID.fullmatch(project_id)
        or not _DEPLOYMENT_ID.fullmatch(team_id)
    ):
        raise ValueError("Vercel callback alias control coordinates are required")
    existing = _read_alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    )
    if existing is not None and existing["deployment_id"] != state["deployment_id"]:
        raise ValueError("EvoLink callback alias points to another deployment")
    if existing is None:
        subprocess.run(
            [
                vercel_cli,
                "alias",
                "set",
                state["deployment_url"],
                state["callback_host"],
                "--token",
                token,
            ],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )
    readback = _read_alias_binding(
        state,
        token=token,
        project_id=project_id,
        team_id=team_id,
        client=client,
    )
    if readback is None or readback["deployment_id"] != state["deployment_id"]:
        raise ValueError("EvoLink callback alias read-back mismatch")
    result = _verify_runtime(
        state,
        bypass_secret=bypass_secret,
        probe_secret=probe_secret,
        client=client,
    )
    result["binding_action"] = (
        "ALREADY_BOUND" if existing is not None else "BOUND_EXACT_ALIAS"
    )
    return result


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan = subparsers.add_parser("plan")
    bind = subparsers.add_parser("bind")
    for command in (plan, bind):
        command.add_argument("--source-sha", required=True)
        command.add_argument("--workflow-run-id", required=True)
        command.add_argument("--workflow-attempt", type=int, required=True)
        command.add_argument("--output", required=True)
    plan.add_argument("--job-env")
    bind.add_argument("--deployment-url", required=True)
    bind.add_argument("--deployment-id", required=True)
    bind.add_argument("--runtime-bundle-id", required=True)
    bind.add_argument("--vercel-cli", required=True)
    bind.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
    bind.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
    bind.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
    bind.add_argument(
        "--bypass-secret-env",
        default="VERCEL_AUTOMATION_BYPASS_SECRET",
    )
    bind.add_argument(
        "--probe-secret-env",
        default="PROVIDER_GRANT_PROBE_SECRET",
    )
    args = parser.parse_args()
    try:
        if args.action == "plan":
            host = build_callback_host(
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
            )
            result = {
                "callback_host": host,
                "callback_origin": f"https://{host}",
            }
            if args.job_env:
                with Path(args.job_env).open(
                    "a",
                    encoding="utf-8",
                    newline="\n",
                ) as handle:
                    handle.write(f"EVOLINK_CALLBACK_HOST={host}\n")
                    handle.write(f"EVOLINK_CALLBACK_BASE_URL=https://{host}\n")
        else:
            state = build_state(
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                deployment_url=args.deployment_url,
                deployment_id=args.deployment_id,
                runtime_bundle_id=args.runtime_bundle_id,
            )
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                result = bind_alias(
                    state,
                    vercel_cli=args.vercel_cli,
                    token=os.environ.get(args.vercel_token_env, ""),
                    project_id=os.environ.get(args.vercel_project_id_env, ""),
                    team_id=os.environ.get(args.vercel_team_id_env, ""),
                    bypass_secret=os.environ.get(args.bypass_secret_env, ""),
                    probe_secret=os.environ.get(args.probe_secret_env, ""),
                    client=client,
                )
        _write(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        httpx.HTTPError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
