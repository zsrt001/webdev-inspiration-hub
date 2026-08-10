#!/usr/bin/env python3
"""Resolve one exact READY Production deployment from Vercel inspect JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")


def resolve_vercel_inspect(payload: Any, *, expected_url: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Vercel inspect payload must be a JSON object")
    deployment_id = str(payload.get("id") or "").strip()
    if not DEPLOYMENT_ID.fullmatch(deployment_id):
        raise ValueError("Vercel inspect deployment ID is invalid")

    expected = urlsplit(str(expected_url or "").strip())
    if (
        expected.scheme != "https"
        or not expected.hostname
        or expected.username
        or expected.password
        or expected.port is not None
        or expected.path not in {"", "/"}
        or expected.query
        or expected.fragment
    ):
        raise ValueError("expected Vercel deployment URL is invalid")
    observed_host = str(payload.get("url") or "").strip().lower().rstrip(".")
    if observed_host != expected.hostname.lower().rstrip("."):
        raise ValueError("Vercel inspect deployment URL does not match")
    if str(payload.get("readyState") or "").strip().upper() != "READY":
        raise ValueError("Vercel inspect deployment is not READY")
    if str(payload.get("target") or "").strip().lower() != "production":
        raise ValueError("Vercel inspect deployment target is not production")
    return deployment_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--expected-url", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        deployment_id = resolve_vercel_inspect(
            payload,
            expected_url=args.expected_url,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(deployment_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
