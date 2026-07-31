"""Verify one unreadable Vercel Production runtime secret by metadata only."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


_COORDINATE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _targets(record: dict[str, Any]) -> list[str]:
    raw = record.get("target", record.get("targets", []))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError("Vercel runtime secret targets are malformed")
    return sorted(set(raw))


def verify_runtime_secret(
    *,
    token: str,
    project_id: str,
    team_id: str,
    secret_name: str,
    source_sha: str,
    opener=urlopen,
) -> dict[str, Any]:
    if not token.strip():
        raise ValueError("Vercel token is missing")
    if not _COORDINATE.fullmatch(project_id) or not _COORDINATE.fullmatch(team_id):
        raise ValueError("Vercel project coordinates are invalid")
    if secret_name != "EVOLINK_API_KEY":
        raise ValueError("only the EvoLink runtime secret is allowlisted")
    if not _SHA40.fullmatch(source_sha):
        raise ValueError("source SHA is invalid")
    query = urlencode({"teamId": team_id, "target": "production"})
    request = Request(
        f"https://api.vercel.com/v10/projects/{quote(project_id, safe='')}/env?{query}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with opener(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    records = payload if isinstance(payload, list) else payload.get("envs")
    if not isinstance(records, list):
        raise ValueError("Vercel environment metadata is malformed")
    matches = [
        item
        for item in records
        if isinstance(item, dict) and item.get("key", item.get("name")) == secret_name
    ]
    if len(matches) != 1:
        raise ValueError("Vercel EvoLink runtime secret metadata is not unique")
    record = matches[0]
    targets = _targets(record)
    if (
        record.get("type") != "sensitive"
        or "production" not in targets
        or record.get("decrypted") is True
    ):
        raise ValueError("Vercel EvoLink key is not an unreadable Production Sensitive variable")
    return {
        "schema": "vowpic.vercel-runtime-secret-metadata.v1",
        "passed": True,
        "secret_name": secret_name,
        "vercel_secret_type": "sensitive",
        "vercel_target": targets,
        "vercel_value_readable": False,
        "project_id": project_id,
        "team_id": team_id,
        "source_sha": source_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-env", default="VERCEL_TOKEN")
    parser.add_argument("--project-id-env", default="VERCEL_PROJECT_ID")
    parser.add_argument("--team-id-env", default="VERCEL_ORG_ID")
    parser.add_argument("--secret-name", required=True, choices=("EVOLINK_API_KEY",))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = verify_runtime_secret(
        token=os.environ.get(args.token_env, ""),
        project_id=os.environ.get(args.project_id_env, ""),
        team_id=os.environ.get(args.team_id_env, ""),
        secret_name=args.secret_name,
        source_sha=args.source_sha,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
