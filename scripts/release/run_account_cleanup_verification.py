#!/usr/bin/env python3
"""Run protected account cleanup and seal read-after-delete absence evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release._acceptance_evidence import (
    canonical,
    read_private_json,
    signing_key,
    validate_release_binding,
    verify_signed_report,
    write_private_create_once,
)


SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _base_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("account cleanup base URL must be an exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _post_json(
    url: str,
    *,
    token: str,
    payload: dict[str, Any],
    allow_conflict: bool = False,
) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=canonical(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "vowpic-production-acceptance/1",
        },
        method="POST",
    )
    try:
        response = urlopen(request, timeout=45)
        status = int(response.status)
        raw = response.read(1_000_001)
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(1_000_001)
        if not (allow_conflict and status == 409):
            raise ValueError(f"account cleanup endpoint returned HTTP {status}") from exc
    except (OSError, URLError) as exc:
        raise ValueError("account cleanup endpoint was unavailable") from exc
    if len(raw) > 1_000_000:
        raise ValueError("account cleanup response was too large")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("account cleanup response was not JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("account cleanup response was not an object")
    return status, result


def run(
    *,
    base_url: str,
    identity_report_path: Path,
    output_path: Path,
    cron_token: str,
    key: bytes,
    attempts: int = 20,
    retry_seconds: float = 2.0,
) -> dict[str, Any]:
    if len(cron_token.encode("utf-8")) < 24:
        raise ValueError("account cleanup cron token is missing or too short")
    bounded_attempts = max(1, min(30, int(attempts)))
    identity, _raw = read_private_json(
        identity_report_path,
        label="signed identity acceptance report",
    )
    unsigned = verify_signed_report(
        identity,
        key=key,
        expected={
            "schema": "vowpic.linked-commercial-acceptance.v1",
            "phase": "first-login-and-auth-security",
            "passed": True,
        },
        label="signed identity acceptance report",
    )
    validate_release_binding(unsigned)
    user_id = str(UUID(str(unsigned.get("links", {}).get("user_id") or "")))
    base = _base_url(base_url)
    verification_request = {
        "user_id": user_id,
        "source_sha": unsigned["source_sha"],
        "runtime_bundle_id": unsigned["runtime_bundle_id"],
        "deployment_id": unsigned["deployment_id"],
        "manifest_sha256": unsigned["manifest_sha256"],
    }

    absence: dict[str, Any] | None = None
    iteration = 0
    for iteration in range(1, bounded_attempts + 1):
        cleanup_status, cleanup = _post_json(
            f"{base}/api/v1/ops/cleanup_expired_assets",
            token=cron_token,
            payload={},
        )
        if cleanup_status != 200 or cleanup.get("success") is not True:
            raise ValueError("account cleanup did not report success")
        verification_status, candidate = _post_json(
            f"{base}/api/v1/ops/verify_acceptance_media_absence",
            token=cron_token,
            payload=verification_request,
            allow_conflict=True,
        )
        if verification_status == 200:
            absence = candidate
            break
        if iteration < bounded_attempts:
            time.sleep(max(0.0, min(5.0, retry_seconds)))
    if absence is None:
        raise ValueError("account media absence was not proven before the retry limit")

    expected_keys = {
        "schema",
        "passed",
        "source_sha",
        "runtime_bundle_id",
        "deployment_id",
        "manifest_sha256",
        "user_subject_hmac_sha256",
        "verified_asset_count",
        "storage_read_outcome",
        "facts_sha256",
    }
    if set(absence) != expected_keys:
        raise ValueError("account media absence response fields are not exact")
    expected_binding = {
        name: unsigned[name]
        for name in (
            "source_sha",
            "runtime_bundle_id",
            "deployment_id",
            "manifest_sha256",
            "user_subject_hmac_sha256",
        )
    }
    if (
        absence.get("schema") != "vowpic.acceptance-media-absence.v1"
        or absence.get("passed") is not True
        or any(absence.get(name) != value for name, value in expected_binding.items())
        or not isinstance(absence.get("verified_asset_count"), int)
        or int(absence["verified_asset_count"]) < 1
        or absence.get("storage_read_outcome") != "NOT_FOUND"
        or not SHA64.fullmatch(str(absence.get("facts_sha256") or ""))
    ):
        raise ValueError("account media absence response is invalid")

    report = {
        "schema": "vowpic.acceptance-media-absence-proof.v1",
        "passed": True,
        **expected_binding,
        "verified_asset_count": absence["verified_asset_count"],
        "storage_read_outcome": "NOT_FOUND",
        "facts_sha256": absence["facts_sha256"],
        "cleanup_iterations": iteration,
        "produced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    signature = hmac.new(
        key,
        canonical(report).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    sealed = {**report, "signature": f"hmac-sha256:{signature}"}
    write_private_create_once(output_path, sealed)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--identity-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--cron-token-env",
        default="CLEANUP_CRON_TOKEN",
    )
    parser.add_argument(
        "--signing-key-env",
        default="ACCEPTANCE_EVIDENCE_SIGNING_KEY",
    )
    parser.add_argument("--attempts", type=int, default=20)
    args = parser.parse_args()
    try:
        run(
            base_url=args.base_url,
            identity_report_path=Path(args.identity_report),
            output_path=Path(args.output),
            cron_token=os.environ.get(args.cron_token_env, ""),
            key=signing_key(args.signing_key_env),
            attempts=args.attempts,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
