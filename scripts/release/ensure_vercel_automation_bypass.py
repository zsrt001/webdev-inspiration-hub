#!/usr/bin/env python3
"""Ensure the one protected VowPic CI deployment-protection bypass exists."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.vercel_firewall import EdgeLockdownError, VercelFirewallApi  # noqa: E402


NOTE = "VowPic safe baseline CI"
DEPLOYMENT_BYPASS_HEADER_NAME = "x-vercel-protection-bypass"


def parse_bypass_header(raw: str) -> str:
    name, separator, value = raw.strip().partition(":")
    if (
        not separator
        or name.strip().lower() != DEPLOYMENT_BYPASS_HEADER_NAME
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value.strip())
        or "\n" in raw
        or "\r" in raw
    ):
        raise ValueError(
            f"protected bypass must be one {DEPLOYMENT_BYPASS_HEADER_NAME}: <secret> header"
        )
    return value.strip()


def _automation_bypasses(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = project.get("protectionBypass")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise EdgeLockdownError("Vercel project protection bypass inventory is malformed")
    result: dict[str, dict[str, Any]] = {}
    for secret, metadata in raw.items():
        if not isinstance(secret, str) or not isinstance(metadata, dict):
            raise EdgeLockdownError("Vercel project protection bypass entry is malformed")
        if metadata.get("scope") == "automation-bypass":
            result[secret] = metadata
    return result


def ensure_automation_bypass(
    api: VercelFirewallApi,
    secret: str,
) -> dict[str, Any]:
    before = api.project()
    if (
        str(before.get("id") or "") != api.project_id
        or str(before.get("accountId") or "") != api.team_id
    ):
        raise EdgeLockdownError("Vercel project resolved outside the protected project or team")
    existing = _automation_bypasses(before)
    unexpected = sorted(value for value in existing if value != secret)
    if unexpected:
        raise EdgeLockdownError(
            "an unrelated Vercel automation bypass exists; refusing to overwrite or revoke it"
        )
    created = False
    if secret not in existing:
        response = api.generate_automation_bypass(secret=secret, note=NOTE)
        returned = response.get("protectionBypass")
        if not isinstance(returned, dict) or secret not in returned:
            raise EdgeLockdownError("Vercel did not confirm the requested automation bypass")
        created = True
    after = _automation_bypasses(api.project())
    if set(after) != {secret} or after[secret].get("scope") != "automation-bypass":
        raise EdgeLockdownError("Vercel automation bypass readback is not exact")
    return {
        "state": "READY",
        "created": created,
        "automation_secret_count": 1,
        "secret_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "scope": "automation-bypass",
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--header-env", default="VERCEL_AUTOMATION_BYPASS_HEADER")
    parser.add_argument("--token-env", default="VERCEL_TOKEN")
    parser.add_argument("--project-id-env", default="VERCEL_PROJECT_ID")
    parser.add_argument("--team-id-env", default="VERCEL_ORG_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    header = os.environ.get(args.header_env, "")
    token = os.environ.get(args.token_env, "").strip()
    project_id = os.environ.get(args.project_id_env, "").strip()
    team_id = os.environ.get(args.team_id_env, "").strip()
    if not all((header, token, project_id, team_id)):
        print("NOT_RUN: protected Vercel bypass inputs are required", file=sys.stderr)
        return 3
    secret = ""
    api: VercelFirewallApi | None = None
    try:
        secret = parse_bypass_header(header)
        api = VercelFirewallApi(token=token, project_id=project_id, team_id=team_id)
        report = ensure_automation_bypass(api, secret)
        _write_create_once(Path(args.output), report)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, httpx.HTTPError, EdgeLockdownError) as exc:
        detail = str(exc)
        for protected in (token, header, secret):
            if protected:
                detail = detail.replace(protected, "[REDACTED]")
        print(f"FAIL: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    finally:
        if api is not None:
            api.close()
    print(json.dumps({"state": "READY", "output": args.output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
