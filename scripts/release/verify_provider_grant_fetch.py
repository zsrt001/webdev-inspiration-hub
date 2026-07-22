#!/usr/bin/env python3
"""Prove one real Evolink request fetched one activation-bound private grant."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from scripts.release.verify_provider_capabilities import validate_provider_capabilities

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME = re.compile(r"^rtb_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_GRANT_PATH = re.compile(r"^/api/v1/media/grants/[A-Za-z0-9_-]{43,128}$")
_PROVIDER_HOST = re.compile(
    r"^vowpic-provider-[0-9a-f]{12}-[1-9][0-9]{0,19}-[1-9][0-9]{0,9}\.vercel\.app$"
)


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _validate_grant_reference(reference: dict[str, Any], *, expected_source_sha: str) -> None:
    required = {
        "schema", "activation_id", "case_id", "source_sha", "runtime_bundle_id", "api_deployment_id",
        "worker_deployment_id", "worker_image_digest", "grant_id", "asset_id", "job_id",
        "attempt_id", "read_url",
    }
    if not isinstance(reference, dict) or set(reference) != required:
        raise ValueError("Provider grant reference schema is invalid")
    if reference.get("schema") != "vowpic.provider-grant-reference.v1":
        raise ValueError("Provider grant reference version is invalid")
    for field in ("activation_id", "case_id", "grant_id", "asset_id", "job_id", "attempt_id"):
        UUID(str(reference.get(field) or ""))
    if reference.get("source_sha") != expected_source_sha:
        raise ValueError("Provider grant reference source SHA mismatch")
    if not _RUNTIME.fullmatch(str(reference.get("runtime_bundle_id") or "")):
        raise ValueError("Provider grant reference runtime ID is invalid")
    for field in ("api_deployment_id", "worker_deployment_id"):
        if not _DEPLOYMENT.fullmatch(str(reference.get(field) or "")):
            raise ValueError(f"Provider grant reference {field} is invalid")
    if not _DIGEST.fullmatch(str(reference.get("worker_image_digest") or "")):
        raise ValueError("Provider grant reference Worker digest is invalid")
    parsed = urlsplit(str(reference.get("read_url") or ""))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not _PROVIDER_HOST.fullmatch(parsed.hostname.lower())
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not _GRANT_PATH.fullmatch(parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Provider grant read URL is not the exact isolated token route")
    if parsed.hostname.split("-", 3)[2] != expected_source_sha[:12]:
        raise ValueError("Provider grant origin source prefix mismatch")


def _task_id(payload: Any) -> str:
    body = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    task_id = (body.get("task_id") or body.get("id")) if isinstance(body, dict) else None
    clean = str(task_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", clean):
        raise ValueError("Evolink response omitted a valid task ID")
    return clean


def _wait_for_provider_terminal_state(
    *,
    client: httpx.Client,
    api_origin: str,
    api_key: str,
    task_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[str, int]:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    polls = 0
    while True:
        response = client.get(
            f"{api_origin}/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        polls += 1
        if response.is_redirect or response.status_code < 200 or response.status_code >= 300:
            raise ValueError(
                f"Evolink Provider task query failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError("Evolink Provider task query response is invalid") from exc
        if not isinstance(payload, dict) or _task_id(payload) != task_id:
            raise ValueError("Evolink Provider task query coordinates mismatch")
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"pending", "processing", "completed", "failed"}:
            raise ValueError("Evolink Provider task query status is invalid")
        if status in {"completed", "failed"}:
            return status, polls
        if time.monotonic() >= deadline:
            raise ValueError("Evolink Provider task did not reach a terminal state before timeout")
        time.sleep(max(0.5, min(float(poll_interval_seconds), 10.0)))


def _validate_usage_binding(
    usage: dict[str, Any],
    *,
    reference: dict[str, Any],
) -> None:
    expected = {
        "provider": "evolink",
        "purpose": "generation-input",
        "runtime_bundle_id": reference["runtime_bundle_id"],
        "target_api_deployment_id": reference["api_deployment_id"],
    }
    if not isinstance(usage, dict) or any(usage.get(key) != value for key, value in expected.items()):
        raise ValueError("Provider grant database binding mismatch")


def _validate_initial_usage(usage: dict[str, Any], *, reference: dict[str, Any]) -> None:
    _validate_usage_binding(usage, reference=reference)
    if usage.get("used_count") != 0 or usage.get("last_used_at") is not None:
        raise ValueError("Provider fetch proof requires an unused grant")


def _validate_usage(
    usage: dict[str, Any],
    *,
    reference: dict[str, Any],
    now: datetime,
) -> int:
    _validate_usage_binding(usage, reference=reference)
    used_count = usage.get("used_count")
    if type(used_count) is not int or used_count != 1:
        raise ValueError("Provider grant must be fetched exactly once")
    try:
        last_used_at = datetime.fromisoformat(str(usage.get("last_used_at") or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Provider grant last-used timestamp is invalid") from exc
    if last_used_at.tzinfo is None or last_used_at.utcoffset() is None:
        raise ValueError("Provider grant last-used timestamp must be timezone-aware")
    age = now.astimezone(timezone.utc) - last_used_at.astimezone(timezone.utc)
    if age < -timedelta(seconds=5) or age > timedelta(minutes=10):
        raise ValueError("Provider grant fetch timestamp is stale or from the future")
    return used_count


def verify_provider_fetch(
    *,
    capability_document: dict[str, Any],
    grant_reference: dict[str, Any],
    expected_source_sha: str,
    api_key: str,
    api_base_url: str,
    image_model: str,
    approval_ref: str,
    signing_key: bytes,
    client: httpx.Client,
    usage_probe: Callable[[str], dict[str, Any]],
    now: datetime | None = None,
    poll_timeout_seconds: float = 90.0,
    poll_interval_seconds: float = 2.0,
    terminal_timeout_seconds: float = 600.0,
    terminal_poll_interval_seconds: float = 3.0,
) -> dict[str, Any]:
    source = str(expected_source_sha or "").strip().lower()
    if not _SHA40.fullmatch(source):
        raise ValueError("Provider fetch source SHA is invalid")
    validate_provider_capabilities(capability_document)
    _validate_grant_reference(grant_reference, expected_source_sha=source)
    if not str(api_key or "").strip():
        raise ValueError("Evolink API key is required")
    parsed_base = urlsplit(str(api_base_url or "").strip().rstrip("/"))
    if (
        parsed_base.scheme != "https" or not parsed_base.hostname
        or parsed_base.username is not None or parsed_base.password is not None
        or parsed_base.port is not None or parsed_base.path not in {"", "/"}
        or parsed_base.query or parsed_base.fragment
    ):
        raise ValueError("Evolink API base URL must be an exact HTTPS origin")
    model = str(image_model or "").strip()
    approval = str(approval_ref or "").strip()
    if not model or not approval or len(signing_key) < 32:
        raise ValueError("Provider fetch model, approval, and signing key are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Provider fetch verification timestamp must be timezone-aware")

    initial_usage = usage_probe(str(grant_reference["grant_id"]))
    _validate_initial_usage(initial_usage, reference=grant_reference)

    api_origin = f"{parsed_base.scheme}://{parsed_base.hostname}"
    response = client.post(
        f"{api_origin}/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "prompt": "Provider fetch verification only; one private source image; no publication.",
            "image_urls": [grant_reference["read_url"]],
            "size": "3:4",
            "quality": "standard",
            "model_params": {"web_search": False},
        },
    )
    if response.is_redirect or response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"Evolink Provider fetch request failed with HTTP {response.status_code}")
    try:
        provider_task_id = _task_id(response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Evolink Provider fetch response is invalid") from exc

    deadline = time.monotonic() + max(0.0, float(poll_timeout_seconds))
    usage: dict[str, Any] = {}
    while True:
        usage = usage_probe(str(grant_reference["grant_id"]))
        used_count_value = usage.get("used_count") if isinstance(usage, dict) else None
        if type(used_count_value) is int and used_count_value >= 1:
            break
        if time.monotonic() >= deadline:
            raise ValueError("Evolink did not fetch the private grant before the deadline")
        time.sleep(max(0.1, min(float(poll_interval_seconds), 5.0)))
    observed_at = current if now is not None else datetime.now(timezone.utc)
    used_count = _validate_usage(usage, reference=grant_reference, now=observed_at)
    terminal_status, terminal_poll_count = _wait_for_provider_terminal_state(
        client=client,
        api_origin=api_origin,
        api_key=api_key,
        task_id=provider_task_id,
        timeout_seconds=terminal_timeout_seconds,
        poll_interval_seconds=terminal_poll_interval_seconds,
    )

    unsigned = {
        "schema": "vowpic.preview-provider-fetch-report.v1",
        "passed": True,
        "provider": "evolink",
        "source_sha": source,
        "runtime_bundle_id": grant_reference["runtime_bundle_id"],
        "api_deployment_id": grant_reference["api_deployment_id"],
        "worker_deployment_id": grant_reference["worker_deployment_id"],
        "worker_image_digest": grant_reference["worker_image_digest"],
        "activation_id": grant_reference["activation_id"],
        "grant_id_hash": hashlib.sha256(str(grant_reference["grant_id"]).encode()).hexdigest(),
        "grant_reference_sha256": hashlib.sha256(_canonical(grant_reference)).hexdigest(),
        "provider_task_id_hash": hashlib.sha256(provider_task_id.encode()).hexdigest(),
        "provider_task_terminal_status": terminal_status,
        "provider_task_terminal_poll_count": terminal_poll_count,
        "network_submit_count": 1,
        "provider_fetch_count": used_count,
        "provider_capabilities_sha256": hashlib.sha256(_canonical(capability_document)).hexdigest(),
        "approval_ref": approval,
        "produced_at": current.astimezone(timezone.utc).isoformat(),
    }
    signature = hmac.new(signing_key, _canonical(unsigned), hashlib.sha256).hexdigest()
    return {**unsigned, "signature": f"hmac-sha256:{signature}"}


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    if not clean:
        raise ValueError("Preview database URL is required")
    return clean


def _usage_probe(database_url: str, grant_id: str) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    UUID(grant_id)
    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT provider, purpose, runtime_bundle_id, target_api_deployment_id,
                       used_count, last_used_at
                FROM asset_access_grants WHERE id = %s
                """,
                (grant_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError("Provider grant database row is missing")
    result = dict(row)
    if result.get("last_used_at") is not None:
        result["last_used_at"] = result["last_used_at"].isoformat()
    return result


def _revoke_grant(database_url: str, reference: dict[str, Any]) -> None:
    import psycopg2

    UUID(str(reference.get("grant_id") or ""))
    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE asset_access_grants
                SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                WHERE id = %s AND provider = 'evolink' AND purpose = 'generation-input'
                  AND asset_id = %s AND job_id = %s AND attempt_id = %s
                  AND runtime_bundle_id = %s AND target_api_deployment_id = %s
                RETURNING id
                """,
                (
                    reference["grant_id"], reference["asset_id"], reference["job_id"],
                    reference["attempt_id"], reference["runtime_bundle_id"],
                    reference["api_deployment_id"],
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("Provider grant revocation coordinates mismatch")


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return completed.stdout.strip().lower()


def _load_object(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider fetch input must be a JSON object")
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", default="release/provider-capabilities.json")
    parser.add_argument("--grant-reference", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_CONTROL_DATABASE_URL")
    parser.add_argument("--api-key-env", default="EVOLINK_API_KEY")
    parser.add_argument("--api-base-url-env", default="EVOLINK_API_BASE_URL")
    parser.add_argument("--image-model-env", default="EVOLINK_IMAGE_MODEL")
    parser.add_argument("--approval-id-env", default="PREVIEW_COMMERCIAL_APPROVAL_ID")
    parser.add_argument("--signing-key-env", default="PROVIDER_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = args.expected_source_sha.strip().lower()
        if _git_head() != source:
            raise ValueError("Provider fetch source SHA is not the current checkout")
        capability_document = _load_object(args.capabilities)
        validate_provider_capabilities(capability_document)
        grant_reference = _load_object(args.grant_reference)
        database_url = os.environ.get(args.database_url_env, "")
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            try:
                report = verify_provider_fetch(
                    capability_document=capability_document,
                    grant_reference=grant_reference,
                    expected_source_sha=source,
                    api_key=os.environ.get(args.api_key_env, ""),
                    api_base_url=os.environ.get(args.api_base_url_env, ""),
                    image_model=os.environ.get(args.image_model_env, ""),
                    approval_ref=os.environ.get(args.approval_id_env, ""),
                    signing_key=os.environ.get(args.signing_key_env, "").encode("utf-8"),
                    client=client,
                    usage_probe=lambda grant_id: _usage_probe(database_url, grant_id),
                )
            finally:
                _revoke_grant(database_url, grant_reference)
        _write_create_once(Path(args.output), report)
        print(json.dumps({"passed": True, "report_sha256": hashlib.sha256(_canonical(report)).hexdigest()}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
