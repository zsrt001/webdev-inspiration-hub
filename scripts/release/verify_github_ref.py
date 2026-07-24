#!/usr/bin/env python3
"""Fail closed unless an authenticated GitHub ref still points at one exact SHA."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"


class GitHubRefVerificationError(RuntimeError):
    pass


def _repository(value: str) -> str:
    clean = str(value or "").strip()
    parts = clean.split("/")
    if len(parts) != 2 or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts
    ):
        raise ValueError("repository must be an owner/name pair")
    return clean


def _ref(value: str) -> str:
    clean = str(value or "").strip()
    if (
        not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]{1,200}", clean)
        or ".." in clean
        or "//" in clean
        or clean.endswith(("/", "."))
    ):
        raise ValueError("ref must be one canonical refs/heads name")
    return clean


def _sha(value: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", clean):
        raise ValueError("expected SHA must be 40 lowercase hexadecimal characters")
    return clean


def _read_payload(
    *,
    url: str,
    headers: dict[str, str],
    client: Any | None,
) -> tuple[int, Any]:
    if client is not None:
        try:
            response = client.get(url, headers=headers)
            status_code = int(response.status_code)
            if status_code != 200:
                return status_code, None
            return status_code, response.json()
        except ValueError as exc:
            raise GitHubRefVerificationError(
                "GitHub ref read returned invalid JSON"
            ) from exc
        except Exception as exc:
            raise GitHubRefVerificationError("GitHub ref read failed") from exc

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20.0) as response:
            status_code = int(response.status)
            body = response.read()
    except HTTPError as exc:
        return int(exc.code), None
    except (OSError, TimeoutError, URLError) as exc:
        raise GitHubRefVerificationError("GitHub ref read failed") from exc
    if status_code != 200:
        return status_code, None
    try:
        return status_code, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubRefVerificationError(
            "GitHub ref read returned invalid JSON"
        ) from exc


def verify_ref(
    *,
    repository: str,
    ref: str,
    expected_sha: str,
    token: str,
    client: Any | None = None,
) -> dict[str, str | bool]:
    repository_name = _repository(repository)
    canonical_ref = _ref(ref)
    expected = _sha(expected_sha)
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("GitHub token is required")
    api_ref = quote(canonical_ref.removeprefix("refs/"), safe="/")
    status_code, payload = _read_payload(
        url=f"{API_BASE}/repos/{repository_name}/git/ref/{api_ref}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {clean_token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "vowpic-ref-guard/1",
        },
        client=client,
    )
    if status_code != 200:
        raise GitHubRefVerificationError(
            f"GitHub ref read failed with HTTP {status_code}"
        )
    target = payload.get("object") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ref") != canonical_ref
        or not isinstance(target, dict)
        or target.get("type") != "commit"
    ):
        raise GitHubRefVerificationError("GitHub ref response has invalid coordinates")
    actual = str(target.get("sha") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", actual):
        raise GitHubRefVerificationError("GitHub ref response has an invalid commit SHA")
    if actual != expected:
        raise GitHubRefVerificationError(
            f"GitHub ref drifted from expected SHA {expected} to {actual}"
        )
    return {
        "passed": True,
        "repository": repository_name,
        "ref": canonical_ref,
        "sha": actual,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        token = os.environ.get(args.token_env, "")
        result = verify_ref(
            repository=args.repository,
            ref=args.ref,
            expected_sha=args.expected_sha,
            token=token,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, sort_keys=True, indent=2)
            handle.write("\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (GitHubRefVerificationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
