#!/usr/bin/env python3
"""Resolve protected Google coordinates to the exact Supabase subjects used by runtime auth."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

import httpx


MANAGEMENT_API = "https://api.supabase.com"


def _normalize_subject(value: str) -> str:
    clean = str(value or "").strip()
    try:
        return str(UUID(clean))
    except ValueError:
        return clean


def _headers(token: str) -> dict[str, str]:
    clean = str(token or "").strip()
    if not clean:
        raise ValueError("Supabase management token is required")
    return {
        "Authorization": f"Bearer {clean}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "vowpic-google-subject-resolution/1",
    }


def load_requested_subjects(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("exactly two protected Google subjects are required")
    subjects: list[str] = []
    for item in payload:
        if isinstance(item, str):
            provider, subject = "google", item
        elif isinstance(item, dict) and set(item) == {"provider", "subject"}:
            provider, subject = str(item["provider"]), str(item["subject"])
        else:
            raise ValueError("protected Google subject schema is invalid")
        clean_provider = provider.strip().lower()
        clean_subject = _normalize_subject(subject)
        if clean_provider != "google" or not clean_subject or len(clean_subject) > 512:
            raise ValueError("only two non-empty protected Google subjects are allowed")
        subjects.append(clean_subject)
    if len(set(subjects)) != 2:
        raise ValueError("protected Google subjects must be distinct")
    return subjects


def resolve_requested_subjects(
    requested: list[str],
    identity_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    normalized: list[dict[str, str]] = []
    for row in identity_rows:
        if str(row.get("provider") or "").strip().lower() != "google":
            continue
        try:
            user_id = str(UUID(str(row.get("user_id") or "").strip()))
        except ValueError as exc:
            raise ValueError("Supabase Google identity has an invalid user ID") from exc
        normalized.append(
            {
                "user_id": user_id,
                "provider_id": str(row.get("provider_id") or "").strip(),
                "identity_sub": str(row.get("identity_sub") or "").strip(),
            }
        )

    resolved: list[dict[str, str]] = []
    modes = {"supabase_user_id": 0, "google_provider_subject": 0}
    for subject in requested:
        matches = {
            row["user_id"]
            for row in normalized
            if subject in {row["user_id"], row["provider_id"], row["identity_sub"]}
        }
        if len(matches) != 1:
            raise ValueError("each protected Google subject must resolve to exactly one Supabase user")
        user_id = next(iter(matches))
        mode = "supabase_user_id" if subject == user_id else "google_provider_subject"
        modes[mode] += 1
        resolved.append({"provider": "google", "subject": user_id})
    if len({item["subject"] for item in resolved}) != 2:
        raise ValueError("protected Google subjects must resolve to two distinct Supabase users")
    return resolved, modes


def query_google_identities(
    *,
    project_ref: str,
    token: str,
    requested: list[str],
    client: httpx.Client,
) -> list[dict[str, Any]]:
    clean_ref = str(project_ref or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{10,40}", clean_ref):
        raise ValueError("Supabase project ref is invalid")
    query = """
        SELECT provider, provider_id, user_id::text AS user_id,
               identity_data->>'sub' AS identity_sub
        FROM auth.identities
        WHERE provider = 'google'
          AND (
            provider_id IN ($1, $2)
            OR user_id::text IN ($1, $2)
            OR identity_data->>'sub' IN ($1, $2)
          )
        LIMIT 5
    """
    response = client.post(
        f"{MANAGEMENT_API}/v1/projects/{clean_ref}/database/query/read-only",
        headers=_headers(token),
        json={"query": query, "parameters": requested},
    )
    if response.status_code != 201:
        raise RuntimeError(
            f"Supabase read-only identity query failed with HTTP {response.status_code}"
        )
    payload = response.json()
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = next(
            (payload[key] for key in ("result", "data", "rows") if isinstance(payload.get(key), list)),
            None,
        )
    else:
        rows = None
    if rows is None or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("Supabase read-only identity query returned an invalid response")
    return [dict(row) for row in rows]


def _write_create_once(path: Path, payload: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    if private:
        path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects-file", required=True)
    parser.add_argument("--project-ref-env", default="SUPABASE_PROJECT_REF")
    parser.add_argument("--token-env", default="SUPABASE_AUTH_CONFIG_TOKEN")
    parser.add_argument("--resolved-output", required=True)
    parser.add_argument("--report-output", required=True)
    args = parser.parse_args()
    try:
        requested = load_requested_subjects(Path(args.subjects_file))
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            rows = query_google_identities(
                project_ref=os.environ.get(args.project_ref_env, ""),
                token=os.environ.get(args.token_env, ""),
                requested=requested,
                client=client,
            )
        resolved, modes = resolve_requested_subjects(requested, rows)
        _write_create_once(Path(args.resolved_output), resolved, private=True)
        report = {
            "schema": "vowpic.google-subject-resolution.v1",
            "passed": True,
            "requested_count": 2,
            "resolved_count": 2,
            "distinct_supabase_users": 2,
            "resolution_modes": modes,
        }
        _write_create_once(Path(args.report_output), report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"Google subject resolution failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
