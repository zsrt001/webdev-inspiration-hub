"""Verify one exact successful GitHub Actions workflow attempt read-only."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx


REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_WORKFLOW_PATH = ".github/workflows/integration.yml"
WORKFLOW_EVENTS = {
    EXPECTED_WORKFLOW_PATH: "workflow_dispatch",
    ".github/workflows/google-auth-protected-privacy.yml": "push",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def validate_workflow_run(
    payload: dict[str, Any],
    *,
    repository: str,
    run_id: str,
    run_attempt: int,
    source_sha: str,
    workflow_path: str = EXPECTED_WORKFLOW_PATH,
) -> dict[str, Any]:
    expected_event = WORKFLOW_EVENTS.get(workflow_path)
    if (
        not REPOSITORY.fullmatch(repository)
        or not run_id.isdigit()
        or int(run_id) < 1
        or run_attempt < 1
        or not SHA40.fullmatch(source_sha)
        or expected_event is None
    ):
        raise ValueError("GitHub workflow coordinates are invalid")
    updated_at = datetime.fromisoformat(
        str(payload.get("updated_at") or "").replace("Z", "+00:00")
    )
    repository_payload = payload.get("repository")
    if (
        not isinstance(repository_payload, dict)
        or str(payload.get("id")) != run_id
        or int(payload.get("run_attempt") or 0) != run_attempt
        or payload.get("head_sha") != source_sha
        or payload.get("head_branch") != "main"
        or payload.get("event") != expected_event
        or payload.get("status") != "completed"
        or payload.get("conclusion") != "success"
        or payload.get("path") != workflow_path
        or repository_payload.get("full_name") != repository
        or updated_at.tzinfo is None
        or updated_at.utcoffset() is None
    ):
        raise ValueError("GitHub workflow attempt is not the exact successful Preview run")
    exact = {
        "repository": repository,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": source_sha,
        "head_branch": "main",
        "event": expected_event,
        "status": "completed",
        "conclusion": "success",
        "path": workflow_path,
        "updated_at": updated_at.isoformat(),
    }
    return {
        "schema": "vowpic.github-workflow-run-verification.v1",
        "passed": True,
        **exact,
        "metadata_sha256": hashlib.sha256(_canonical(exact)).hexdigest(),
    }


def verify_workflow_run(
    *,
    repository: str,
    run_id: str,
    run_attempt: int,
    source_sha: str,
    token: str,
    workflow_path: str = EXPECTED_WORKFLOW_PATH,
) -> dict[str, Any]:
    if not token:
        raise ValueError("GitHub token is missing")
    url = (
        f"https://api.github.com/repos/{repository}/actions/runs/"
        f"{run_id}/attempts/{run_attempt}"
    )
    with httpx.Client(
        timeout=30.0,
        follow_redirects=False,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    ) as client:
        response = client.get(url)
    if response.status_code != 200 or response.history:
        raise ValueError("GitHub workflow attempt metadata is unavailable")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("GitHub workflow attempt metadata is invalid")
    return validate_workflow_run(
        payload,
        repository=repository,
        run_id=run_id,
        run_attempt=run_attempt,
        source_sha=source_sha,
        workflow_path=workflow_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument(
        "--workflow-path",
        choices=sorted(WORKFLOW_EVENTS),
        default=EXPECTED_WORKFLOW_PATH,
    )
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = verify_workflow_run(
            repository=args.repository.strip(),
            run_id=args.run_id.strip(),
            run_attempt=args.run_attempt,
            source_sha=args.source_sha.strip().lower(),
            token=os.environ.get(args.token_env, "").strip(),
            workflow_path=args.workflow_path,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"schema": result["schema"], "passed": True}))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        httpx.HTTPError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
