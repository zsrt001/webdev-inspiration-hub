#!/usr/bin/env python3
"""Resolve the public Supabase browser runtime for one protected Preview project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx


MANAGEMENT_API = "https://api.supabase.com"
REPORT_SCHEMA = "vowpic.preview-supabase-runtime.v1"
PROJECT_REF_RE = re.compile(r"[a-z0-9]{10,40}")
REGION_RE = re.compile(r"[a-z]{2}-[a-z]+-[1-9][0-9]*")


def validate_project_ref(value: object) -> str:
    clean = str(value or "").strip().lower()
    if not PROJECT_REF_RE.fullmatch(clean):
        raise ValueError("Supabase project ref is invalid")
    return clean


def _validate_public_key(value: object) -> str:
    clean = str(value or "").strip()
    if len(clean) < 20 or any(char.isspace() for char in clean):
        raise ValueError("Supabase public API key is absent or malformed")
    return clean


def validate_region(value: object) -> str:
    clean = str(value or "").strip().lower()
    if not REGION_RE.fullmatch(clean):
        raise ValueError("Supabase project region is absent or malformed")
    return clean


def read_project_region(
    project_ref: str,
    *,
    token: str,
    client: httpx.Client,
) -> str:
    clean_ref = validate_project_ref(project_ref)
    clean_token = token.strip()
    if not clean_token:
        raise ValueError("Supabase Management API token is required")
    response = client.get(
        f"{MANAGEMENT_API}/v1/projects/{clean_ref}",
        headers={
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/json",
            "User-Agent": "vowpic-preview-supabase-runtime/1",
        },
    )
    if response.status_code != 200:
        raise ValueError(
            f"Supabase project read failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Supabase project read returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Supabase project read returned an invalid record")
    return validate_region(payload.get("region"))


def select_public_key(payload: object) -> dict[str, str]:
    if not isinstance(payload, list):
        raise ValueError("Supabase API key inventory must be a list")

    publishable: list[dict[str, str]] = []
    legacy_anon: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Supabase API key inventory contains an invalid record")
        key_type = str(item.get("type") or "").strip().lower()
        name = str(item.get("name") or "").strip().lower()
        api_key = item.get("api_key")
        if api_key in (None, ""):
            continue
        record = {
            "api_key": _validate_public_key(api_key),
            "id": str(item.get("id") or "").strip(),
            "name": name,
            "prefix": str(item.get("prefix") or "").strip(),
            "type": key_type,
        }
        if key_type == "publishable":
            publishable.append(record)
        elif key_type == "legacy" and name == "anon":
            legacy_anon.append(record)

    candidates = publishable or legacy_anon
    if len(candidates) != 1:
        raise ValueError(
            "Supabase project must expose exactly one preferred publishable "
            "or legacy anon API key"
        )
    return candidates[0]


def read_public_runtime(
    project_ref: str,
    *,
    token: str,
    client: httpx.Client,
) -> tuple[str, dict[str, str]]:
    clean_ref = validate_project_ref(project_ref)
    clean_token = token.strip()
    if not clean_token:
        raise ValueError("Supabase Management API token is required")
    response = client.get(
        f"{MANAGEMENT_API}/v1/projects/{clean_ref}/api-keys",
        params={"reveal": "true"},
        headers={
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/json",
            "User-Agent": "vowpic-preview-supabase-runtime/1",
        },
    )
    if response.status_code != 200:
        raise ValueError(
            f"Supabase API key inventory read failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Supabase API key inventory returned invalid JSON") from exc
    return f"https://{clean_ref}.supabase.co", select_public_key(payload)


def build_report(
    project_ref: str,
    supabase_url: str,
    public_key: dict[str, str],
    *,
    pooler_region: str,
) -> dict[str, str]:
    clean_ref = validate_project_ref(project_ref)
    expected_url = f"https://{clean_ref}.supabase.co"
    if supabase_url != expected_url:
        raise ValueError("Supabase runtime URL does not match the Preview project ref")
    api_key = _validate_public_key(public_key.get("api_key"))
    return {
        "schema": REPORT_SCHEMA,
        "project_ref": clean_ref,
        "pooler_region": validate_region(pooler_region),
        "supabase_url": expected_url,
        "public_key_id": str(public_key.get("id") or ""),
        "public_key_name": str(public_key.get("name") or ""),
        "public_key_prefix": str(public_key.get("prefix") or ""),
        "public_key_type": str(public_key.get("type") or ""),
        "public_key_sha256": hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
    }


def write_job_env(
    path: Path,
    *,
    supabase_url: str,
    api_key: str,
    pooler_region: str,
) -> None:
    clean_key = _validate_public_key(api_key)
    clean_region = validate_region(pooler_region)
    if "\n" in supabase_url or "\r" in supabase_url:
        raise ValueError("Supabase runtime URL contains a line break")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"PREVIEW_SUPABASE_URL={supabase_url}\n")
        handle.write(f"PREVIEW_SUPABASE_PUBLISHABLE_KEY={clean_key}\n")
        handle.write(f"PREVIEW_SUPABASE_POOLER_REGION={clean_region}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--token-env", default="SUPABASE_MANAGEMENT_TOKEN")
    parser.add_argument("--job-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "")
    with httpx.Client(timeout=30.0) as client:
        pooler_region = read_project_region(
            args.project_ref,
            token=token,
            client=client,
        )
        supabase_url, public_key = read_public_runtime(
            args.project_ref,
            token=token,
            client=client,
        )
    report = build_report(
        args.project_ref,
        supabase_url,
        public_key,
        pooler_region=pooler_region,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    api_key = public_key["api_key"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{api_key}")
    write_job_env(
        args.job_env,
        supabase_url=supabase_url,
        api_key=api_key,
        pooler_region=pooler_region,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
