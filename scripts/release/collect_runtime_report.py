#!/usr/bin/env python3
"""Collect and sign a sanitized runtime report from one exact protected deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.build_manifest import validate_manifest


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _exact_base_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("runtime report requires one exact HTTPS deployment origin")
    return f"https://{parsed.netloc}"


def _get_json(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
) -> tuple[dict[str, Any], str]:
    response = client.get(url, headers=headers)
    if response.history or 300 <= response.status_code < 400:
        raise ValueError("runtime report endpoint redirected")
    if response.status_code != 200:
        raise ValueError(f"runtime report endpoint failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("runtime report endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("runtime report endpoint must return a JSON object")
    return payload, hashlib.sha256(response.content).hexdigest()


def _expected_version(manifest: dict[str, Any]) -> dict[str, Any]:
    role = manifest["release_role"]
    return {
        "schema": "vowpic.runtime-bundle-report.v1",
        "source_sha": manifest["source_sha"],
        "runtime_bundle_id": manifest["runtime_bundle_id"],
        "deployment_id": manifest["api_deployment_id"],
        "release_role": role,
        "runtime_environment": "preview" if role.startswith("PREVIEW_") else "production",
        "schema_revision": manifest["schema_revision"],
        "api_compatibility_version": manifest["api_compatibility_version"],
        "worker_compatibility_version": manifest["worker_compatibility_version"],
        "job_payload_min": manifest["job_payload_min"],
        "job_payload_max": manifest["job_payload_max"],
        "worker_image_digest": manifest["worker_image_digest"] or "",
        "provider_policy_hash": manifest["contract_hashes"]["provider"],
        "flag_contract_hash": manifest["contract_hashes"]["flag"],
    }


def collect_api_runtime_report(
    client: httpx.Client,
    *,
    base_url: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    bypass_secret: str,
    signing_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = validate_manifest(manifest)
    manifest_hash = str(manifest_sha256 or "").strip().lower()
    if len(manifest_hash) != 64 or any(character not in "0123456789abcdef" for character in manifest_hash):
        raise ValueError("manifest SHA-256 is invalid")
    if len(signing_key) < 32:
        raise ValueError("runtime report signing key is missing or too short")
    origin = _exact_base_url(base_url)
    headers = {"Accept": "application/json"}
    if bypass_secret.strip():
        headers["x-vercel-protection-bypass"] = bypass_secret.strip()
    liveness, liveness_sha = _get_json(client, f"{origin}/health", headers=headers)
    readiness, readiness_sha = _get_json(client, f"{origin}/health/ready", headers=headers)
    version, version_sha = _get_json(client, f"{origin}/api/v1/version", headers=headers)
    if liveness != {"status": "healthy", "kind": "liveness", "readiness": "/health/ready"}:
        raise ValueError("liveness response contains non-process state or is unhealthy")
    if readiness.get("ready") is not True:
        raise ValueError("runtime readiness did not pass")
    expected_version = _expected_version(normalized)
    if version != expected_version:
        raise ValueError("runtime version does not match the immutable manifest")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    unsigned: dict[str, Any] = {
        "schema": "vowpic.api-runtime-report.v1",
        "manifest_sha256": manifest_hash,
        "release_role": normalized["release_role"],
        "source_sha": normalized["source_sha"],
        "runtime_bundle_id": normalized["runtime_bundle_id"],
        "api_deployment_id": normalized["api_deployment_id"],
        "schema_revision": normalized["schema_revision"],
        "api_compatibility_version": normalized["api_compatibility_version"],
        "worker_compatibility_version": normalized["worker_compatibility_version"],
        "job_payload_min": normalized["job_payload_min"],
        "job_payload_max": normalized["job_payload_max"],
        "worker_image_digest": normalized["worker_image_digest"],
        "provider_policy_hash": normalized["contract_hashes"]["provider"],
        "flag_contract_hash": normalized["contract_hashes"]["flag"],
        "liveness_response_sha256": liveness_sha,
        "readiness_response_sha256": readiness_sha,
        "version_response_sha256": version_sha,
        "observed_at": observed_at,
    }
    signature = hmac.new(signing_key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    return {**unsigned, "signature": f"hmac-sha256:{signature}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--bypass-secret-env", default="VERCEL_AUTOMATION_BYPASS_SECRET")
    parser.add_argument("--signing-key-env", default="RELEASE_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            report = collect_api_runtime_report(
                client,
                base_url=args.base_url,
                manifest=manifest,
                manifest_sha256=args.manifest_sha256,
                bypass_secret=os.environ.get(args.bypass_secret_env, ""),
                signing_key=os.environ.get(args.signing_key_env, "").encode("utf-8"),
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
