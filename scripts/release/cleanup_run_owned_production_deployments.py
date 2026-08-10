#!/usr/bin/env python3
"""Delete Vercel deployments owned by one exact Production workflow attempt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import quote

import httpx


SCHEMA = "vowpic.production-run-deployment-cleanup.v1"


def _identifier(value: str, *, prefix: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(rf"{re.escape(prefix)}[A-Za-z0-9_-]+", normalized):
        raise ValueError(f"{label} is invalid")
    return normalized


def cleanup_run_owned_production_deployments(
    *,
    token: str,
    expected_project_id: str,
    expected_team_id: str,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: str,
    baseline_deployment_id: str,
    max_checks: int = 15,
    min_checks: int = 10,
    stable_zero_checks: int = 3,
    check_interval_seconds: float = 3.0,
) -> dict[str, Any]:
    project_id = _identifier(
        expected_project_id,
        prefix="prj_",
        label="Vercel project ID",
    )
    team_id = _identifier(
        expected_team_id,
        prefix="team_",
        label="Vercel team ID",
    )
    baseline_id = _identifier(
        baseline_deployment_id,
        prefix="dpl_",
        label="baseline deployment ID",
    )
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA is invalid")
    if not workflow_run_id.isdigit() or not workflow_attempt.isdigit():
        raise ValueError("workflow coordinates are invalid")
    normalized_token = token.strip()
    if not normalized_token:
        raise ValueError("Vercel token is missing")
    if (
        max_checks < 1
        or min_checks < 1
        or min_checks > max_checks
        or stable_zero_checks < 1
        or stable_zero_checks > max_checks
        or check_interval_seconds < 0
    ):
        raise ValueError("cleanup polling contract is invalid")

    headers = {"Authorization": f"Bearer {normalized_token}"}
    params = {"projectId": project_id, "teamId": team_id, "limit": "100"}
    deleted_ids: list[str] = []
    checks_completed = 0
    consecutive_zero_checks = 0
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        for check_number in range(1, max_checks + 1):
            listing = client.get(
                "https://api.vercel.com/v6/deployments",
                headers=headers,
                params=params,
            )
            listing.raise_for_status()
            payload = listing.json()
            deployments = (
                payload.get("deployments") if isinstance(payload, dict) else None
            )
            if not isinstance(deployments, list):
                raise ValueError("Vercel deployment listing is invalid")

            owned_ids: set[str] = set()
            for item in deployments:
                if not isinstance(item, dict) or not isinstance(item.get("meta"), dict):
                    continue
                metadata = item["meta"]
                if (
                    str(metadata.get("vowpicWorkflowRunId") or "")
                    != workflow_run_id
                    or str(metadata.get("vowpicWorkflowAttempt") or "")
                    != workflow_attempt
                ):
                    continue
                deployment_id = _identifier(
                    str(item.get("uid") or item.get("id") or ""),
                    prefix="dpl_",
                    label="run-owned deployment ID",
                )
                if deployment_id == baseline_id:
                    raise ValueError(
                        "baseline deployment unexpectedly has run-owned metadata"
                    )
                response_project_id = str(item.get("projectId") or "").strip()
                if (
                    (response_project_id and response_project_id != project_id)
                    or str(metadata.get("vowpicSourceSha") or "") != source_sha
                    or str(metadata.get("vowpicReleaseRole") or "")
                    != "COMMERCIAL_7A"
                ):
                    raise ValueError(
                        "run-owned deployment is outside the Production boundary"
                    )
                owned_ids.add(deployment_id)

            for deployment_id in sorted(owned_ids):
                deletion = client.delete(
                    "https://api.vercel.com/v13/deployments/"
                    f"{quote(deployment_id, safe='')}",
                    headers=headers,
                    params={"teamId": team_id},
                )
                if deletion.status_code not in {200, 204, 404}:
                    deletion.raise_for_status()
                readback = client.get(
                    "https://api.vercel.com/v13/deployments/"
                    f"{quote(deployment_id, safe='')}",
                    headers=headers,
                    params={"teamId": team_id},
                )
                if readback.status_code != 404:
                    raise ValueError(
                        "deleted run-owned deployment did not return 404"
                    )
                if deployment_id not in deleted_ids:
                    deleted_ids.append(deployment_id)

            checks_completed = check_number
            consecutive_zero_checks = (
                0 if owned_ids else consecutive_zero_checks + 1
            )
            if (
                checks_completed >= min_checks
                and consecutive_zero_checks >= stable_zero_checks
            ):
                break
            if check_number < max_checks:
                time.sleep(check_interval_seconds)
        else:
            raise ValueError(
                "run-owned deployments did not reach a stable zero state"
            )

    return {
        "schema": SCHEMA,
        "passed": True,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": int(workflow_attempt),
        "deleted_count": len(deleted_ids),
        "deployment_ids": sorted(deleted_ids),
        "checks_completed": checks_completed,
        "consecutive_zero_checks": consecutive_zero_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-env", default="VERCEL_TOKEN")
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--expected-team-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-attempt", required=True)
    parser.add_argument("--baseline-deployment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get(args.token_env, "").strip()
    try:
        proof = cleanup_run_owned_production_deployments(
            token=token,
            expected_project_id=args.expected_project_id,
            expected_team_id=args.expected_team_id,
            source_sha=args.source_sha,
            workflow_run_id=args.workflow_run_id,
            workflow_attempt=args.workflow_attempt,
            baseline_deployment_id=args.baseline_deployment_id,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "passed": True,
                    "deleted_count": proof["deleted_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        detail = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
