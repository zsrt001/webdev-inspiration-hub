"""Publish database URLs and the cleanup token as Vercel Production secrets."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any


def _run_vercel(
    args: list[str],
    *,
    stdin: str | None = None,
    redact: tuple[str, ...] = (),
) -> str:
    completed = subprocess.run(
        args,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    detail = (completed.stderr or completed.stdout or "Vercel CLI failed").strip()
    for secret in redact:
        if secret:
            detail = detail.replace(secret, "[REDACTED]")
    raise ValueError(f"Vercel CLI failed with exit {completed.returncode}: {detail[:500]}")


def publish_vercel_database_urls(
    *,
    vercel_cli: str,
    token: str,
    project_id: str,
    team_id: str,
    runtime_url: str,
    writer_url: str,
    cleanup_cron_token: str,
) -> dict[str, Any]:
    if not Path(vercel_cli).is_file():
        raise ValueError("pinned Vercel CLI does not exist")
    if not re.fullmatch(r"prj_[A-Za-z0-9]+", project_id):
        raise ValueError("Vercel project ID is invalid")
    if not re.fullmatch(r"team_[A-Za-z0-9]+", team_id):
        raise ValueError("Vercel team ID is invalid")
    if not cleanup_cron_token or "\n" in cleanup_cron_token or "\r" in cleanup_cron_token:
        raise ValueError("cleanup cron token must be a non-empty single-line secret")
    base = [vercel_cli]
    _run_vercel(
        base + ["link", "--yes", "--team", team_id, "--project", project_id, "--token", token],
        redact=(token,),
    )
    for key, value in (
        ("CONTROL_PLANE_DATABASE_URL", writer_url),
        ("DATABASE_URL", runtime_url),
        ("CLEANUP_CRON_TOKEN", cleanup_cron_token),
    ):
        _run_vercel(
            base
            + [
                "env",
                "add",
                key,
                "production",
                "--force",
                "--sensitive",
                "--yes",
                "--token",
                token,
            ],
            stdin=value + "\n",
            redact=(token, runtime_url, writer_url, cleanup_cron_token),
        )
    raw = _run_vercel(
        base + ["env", "list", "production", "--format", "json", "--token", token],
        redact=(token,),
    )
    payload = json.loads(raw)
    entries = payload if isinstance(payload, list) else payload.get("envs", [])
    verified: dict[str, Any] = {}
    for key in ("DATABASE_URL", "CONTROL_PLANE_DATABASE_URL", "CLEANUP_CRON_TOKEN"):
        matches = [item for item in entries if item.get("key", item.get("name")) == key]
        if len(matches) != 1:
            raise ValueError(f"Vercel metadata did not return exactly one {key}")
        item = matches[0]
        targets = item.get("target", item.get("targets", []))
        if isinstance(targets, str):
            targets = [targets]
        if item.get("type") != "sensitive" or "production" not in targets:
            raise ValueError(f"Vercel {key} is not a Production Sensitive variable")
        verified[key] = {"type": "sensitive", "target": ["production"]}
    return verified
