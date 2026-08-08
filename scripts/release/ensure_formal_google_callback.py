#!/usr/bin/env python3
"""Ensure one exact, wildcard-free formal Google OAuth callback in Supabase."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.configure_preview_auth_origin import (
    allow_list_sha256,
    parse_uri_allow_list,
)


MANAGEMENT_API = "https://api.supabase.com"


def exact_callback(base_url: object) -> str:
    parsed = urlsplit(str(base_url or "").strip().rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("formal Google callback requires one exact HTTPS origin")
    return f"https://{parsed.hostname.lower()}/pages/auth/callback"


def _headers(token: str) -> dict[str, str]:
    clean = str(token or "").strip()
    if not clean:
        raise ValueError("Supabase auth configuration token is required")
    return {
        "Authorization": f"Bearer {clean}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "vowpic-formal-google-callback/1",
    }


def ensure_callback(
    *, project_ref: str, token: str, base_url: str, client: httpx.Client
) -> dict[str, object]:
    clean_ref = str(project_ref or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{10,40}", clean_ref):
        raise ValueError("Supabase project ref is invalid")
    callback = exact_callback(base_url)
    url = f"{MANAGEMENT_API}/v1/projects/{clean_ref}/config/auth"
    response = client.get(url, headers=_headers(token))
    if response.status_code != 200:
        raise ValueError(f"Supabase auth config read failed with HTTP {response.status_code}")
    config = response.json()
    if config.get("external_google_enabled") is not True:
        raise ValueError("Supabase Google provider is not enabled")
    current = parse_uri_allow_list(str(config.get("uri_allow_list") or ""))
    if any(any(marker in entry for marker in ("*", "?", "[", "]")) for entry in current):
        raise ValueError("Supabase redirect allow-list contains a wildcard")
    target = current if callback in current else (*current, callback)
    if target != current:
        patched = client.patch(
            url,
            headers=_headers(token),
            json={"uri_allow_list": ",".join(target)},
        )
        if patched.status_code != 200:
            raise ValueError(f"Supabase auth config update failed with HTTP {patched.status_code}")
    readback = client.get(url, headers=_headers(token))
    if readback.status_code != 200:
        raise ValueError(f"Supabase auth config read-back failed with HTTP {readback.status_code}")
    final = parse_uri_allow_list(str(readback.json().get("uri_allow_list") or ""))
    if final != target or callback not in final:
        raise ValueError("formal Google callback did not converge exactly")
    return {
        "schema": "vowpic.formal-google-callback.v1",
        "passed": True,
        "callback_sha256": hashlib.sha256(callback.encode("utf-8")).hexdigest(),
        "allow_list_sha256": allow_list_sha256(",".join(final)),
        "added": target != current,
        "google_provider_enabled": True,
        "wildcard_absent": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project-ref-env", default="SUPABASE_PROJECT_REF")
    parser.add_argument("--token-env", default="SUPABASE_AUTH_CONFIG_TOKEN")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            report = ensure_callback(
                project_ref=os.environ.get(args.project_ref_env, ""),
                token=os.environ.get(args.token_env, ""),
                base_url=args.base_url,
                client=client,
            )
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
