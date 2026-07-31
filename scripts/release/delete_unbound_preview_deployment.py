#!/usr/bin/env python3
"""Delete one exact unbound Preview deployment and prove the 404 readback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


SCHEMA = "vowpic.preview-orphan-deployment-cleanup.v1"
STAGE_SCHEMA = "vowpic.preview-deploy-stage.v1"


def _require_identifier(value: str, prefix: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(rf"{re.escape(prefix)}[A-Za-z0-9]+", normalized):
        raise ValueError(f"{label} is invalid")
    return normalized


def _load_exact_stage(
    path: Path,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
) -> tuple[dict[str, Any], str]:
    stage = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(stage, dict)
        or stage.get("schema") != STAGE_SCHEMA
        or stage.get("source_sha") != source_sha
        or stage.get("workflow_run_id") != workflow_run_id
        or type(stage.get("workflow_attempt")) is not int
        or str(stage["workflow_attempt"]) != workflow_attempt
        or stage.get("deploy_step_outcome") != "success"
        or stage.get("deploy_attempted") is not True
        or stage.get("deployment_url_recorded") is not True
        or stage.get("deployment_bound") is not False
        or stage.get("safe_predeployment_cleanup") is not False
    ):
        raise ValueError("failure stage does not identify one unbound Preview deployment")
    deployment_url = str(stage.get("deployment_url") or "").strip()
    parsed = urlsplit(deployment_url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".vercel.app")
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("failure stage Preview deployment URL is invalid")
    return stage, deployment_url


def delete_unbound_preview_deployment(
    *,
    stage_path: Path,
    token: str,
    expected_project_id: str,
    expected_team_id: str,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
) -> dict[str, Any]:
    project_id = _require_identifier(expected_project_id, "prj_", "Vercel project ID")
    team_id = _require_identifier(expected_team_id, "team_", "Vercel team ID")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA is invalid")
    if not workflow_run_id.isdigit() or not workflow_attempt.isdigit():
        raise ValueError("workflow coordinates are invalid")
    if not token.strip():
        raise ValueError("Vercel token is missing")
    _stage, deployment_url = _load_exact_stage(
        stage_path,
        source_sha=source_sha,
        workflow_run_id=workflow_run_id,
        workflow_attempt=workflow_attempt,
    )
    hostname = str(urlsplit(deployment_url).hostname)
    headers = {"Authorization": f"Bearer {token.strip()}"}
    params = {"teamId": team_id}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        lookup = client.get(
            f"https://api.vercel.com/v13/deployments/{quote(hostname, safe='')}",
            headers=headers,
            params=params,
        )
        lookup.raise_for_status()
        deployment = lookup.json()
        deployment_id = _require_identifier(
            str(deployment.get("uid") or deployment.get("id") or ""),
            "dpl_",
            "Vercel deployment ID",
        )
        metadata = deployment.get("meta")
        response_project_id = str(
            deployment.get("projectId")
            or (
                (deployment.get("project") or {}).get("id")
                if isinstance(deployment.get("project"), dict)
                else ""
            )
            or ""
        ).strip()
        if (
            response_project_id != project_id
            or not isinstance(metadata, dict)
            or metadata.get("vowpicSourceSha") != source_sha
            or metadata.get("vowpicReleaseRole") != "PREVIEW_IDENTITY"
        ):
            raise ValueError("resolved Vercel deployment does not match the Preview boundary")
        deletion = client.delete(
            f"https://api.vercel.com/v13/deployments/{quote(deployment_id, safe='')}",
            headers=headers,
            params=params,
        )
        if deletion.status_code not in {200, 204}:
            deletion.raise_for_status()
        readback = client.get(
            f"https://api.vercel.com/v13/deployments/{quote(deployment_id, safe='')}",
            headers=headers,
            params=params,
        )
        if readback.status_code != 404:
            raise ValueError("deleted Vercel deployment did not return 404")
    return {
        "schema": SCHEMA,
        "state": "DELETED",
        "source_sha": source_sha,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": int(workflow_attempt),
        "deployment_id": deployment_id,
        "deployment_url": deployment_url,
        "project_id": project_id,
        "delete_status": deletion.status_code,
        "readback_status": readback.status_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-stage", type=Path, required=True)
    parser.add_argument("--token-env", default="VERCEL_TOKEN")
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--expected-team-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "").strip()
    try:
        proof = delete_unbound_preview_deployment(
            stage_path=args.failure_stage,
            token=token,
            expected_project_id=args.expected_project_id,
            expected_team_id=args.expected_team_id,
            source_sha=args.source_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"schema": SCHEMA, "state": "DELETED"}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        detail = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
