#!/usr/bin/env python3
"""Prove one real EvoLink submission is recovered only through its deployed callback."""

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


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for entry in (str(ROOT), str(BACKEND)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.services.evolink_service import build_evolink_callback_url  # noqa: E402
from scripts.release.verify_provider_capabilities import (  # noqa: E402
    validate_provider_capabilities,
)
from scripts.release.verify_provider_grant_fetch import (  # noqa: E402
    _canonical,
    _load_object,
    _task_id,
    _validate_grant_reference,
    _validate_initial_usage,
    _validate_usage,
    _wait_for_provider_terminal_state,
)


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_CALLBACK_HOST = re.compile(
    r"^vowpic-evolink-[0-9a-f]{12}-[1-9][0-9]{0,19}-"
    r"[1-9][0-9]{0,9}\.vercel\.app$"
)


def _api_origin(value: object) -> str:
    parsed = urlsplit(str(value or "").strip().rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("EvoLink API base URL must be an exact HTTPS origin")
    return f"https://{parsed.hostname.lower()}"


def _callback_origin(value: object) -> str:
    parsed = urlsplit(str(value or "").strip().rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not _CALLBACK_HOST.fullmatch(parsed.hostname.lower())
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("EvoLink callback origin is not a deployment-bound alias")
    return f"https://{parsed.hostname.lower()}"


def _wait_for_callback_binding(
    *,
    probe: Callable[[], dict[str, Any] | None],
    task_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    polls = 0
    while True:
        row = probe()
        polls += 1
        if isinstance(row, dict):
            if str(row.get("provider_job_id") or "") != task_id:
                raise ValueError("EvoLink callback bound a different Provider task")
            attempt_status = str(row.get("attempt_status") or "")
            job_status = str(row.get("job_status") or "")
            if (
                attempt_status in {"FINISHED", "FAILED"}
                and job_status in {"FINISHED", "FAILED"}
                and row.get("submitted_at")
                and row.get("job_lease_owner") is None
            ):
                return row, polls
        if time.monotonic() >= deadline:
            raise ValueError("EvoLink callback did not bind the lost Provider response")
        sleep(max(0.25, min(float(poll_interval_seconds), 5.0)))


def verify_callback_recovery(
    *,
    capability_document: dict[str, Any],
    grant_reference: dict[str, Any],
    expected_source_sha: str,
    api_key: str,
    api_base_url: str,
    image_model: str,
    callback_origin: str,
    callback_secret: str,
    approval_ref: str,
    signing_key: bytes,
    client: httpx.Client,
    prepare_unknown: Callable[[], None],
    usage_probe: Callable[[str], dict[str, Any]],
    binding_probe: Callable[[], dict[str, Any] | None],
    now: datetime | None = None,
    grant_timeout_seconds: float = 90.0,
    terminal_timeout_seconds: float = 600.0,
    callback_timeout_seconds: float = 90.0,
    poll_interval_seconds: float = 2.0,
) -> dict[str, Any]:
    source = str(expected_source_sha or "").strip().lower()
    if not _SHA40.fullmatch(source):
        raise ValueError("callback recovery source SHA is invalid")
    validate_provider_capabilities(capability_document)
    _validate_grant_reference(grant_reference, expected_source_sha=source)
    api_origin = _api_origin(api_base_url)
    exact_callback_origin = _callback_origin(callback_origin)
    model = str(image_model or "").strip()
    approval = str(approval_ref or "").strip()
    if (
        not str(api_key or "").strip()
        or not model
        or len(str(callback_secret or "").encode("utf-8")) < 32
        or not approval
        or len(signing_key) < 32
    ):
        raise ValueError("callback recovery secrets and approval are required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("callback recovery timestamp must be timezone-aware")
    attempt_id = UUID(str(grant_reference["attempt_id"]))
    callback_url = build_evolink_callback_url(
        attempt_id,
        base_url=exact_callback_origin,
        secret_key=callback_secret,
    )
    initial_usage = usage_probe(str(grant_reference["grant_id"]))
    _validate_initial_usage(initial_usage, reference=grant_reference)

    # This is the acceptance-only lost-response boundary. It records UNKNOWN
    # before the external request and never writes the returned task ID.
    prepare_unknown()
    response = client.post(
        f"{api_origin}/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "prompt": (
                "Provider callback recovery verification only; one private "
                "source image; no publication."
            ),
            "image_urls": [grant_reference["read_url"]],
            "size": "3:4",
            "quality": "standard",
            "model_params": {"web_search": False},
            "callback_url": callback_url,
        },
    )
    if response.is_redirect or not 200 <= response.status_code < 300:
        raise ValueError(
            f"EvoLink callback recovery submit failed with HTTP {response.status_code}"
        )
    try:
        provider_task_id = _task_id(response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("EvoLink callback recovery response is invalid") from exc

    grant_deadline = time.monotonic() + max(0.0, float(grant_timeout_seconds))
    usage: dict[str, Any] = {}
    while True:
        usage = usage_probe(str(grant_reference["grant_id"]))
        used = usage.get("used_count") if isinstance(usage, dict) else None
        if type(used) is int and used >= 1:
            break
        if time.monotonic() >= grant_deadline:
            raise ValueError("EvoLink did not fetch the callback recovery grant")
        time.sleep(max(0.25, min(float(poll_interval_seconds), 5.0)))
    observed_at = current if now is not None else datetime.now(timezone.utc)
    provider_fetch_count = _validate_usage(
        usage,
        reference=grant_reference,
        now=observed_at,
    )
    terminal_status, terminal_polls = _wait_for_provider_terminal_state(
        client=client,
        api_origin=api_origin,
        api_key=api_key,
        task_id=provider_task_id,
        timeout_seconds=terminal_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    binding, callback_polls = _wait_for_callback_binding(
        probe=binding_probe,
        task_id=provider_task_id,
        timeout_seconds=callback_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    unsigned = {
        "schema": "vowpic.preview-provider-fetch-report.v1",
        "passed": True,
        "provider": "evolink",
        "source_sha": source,
        "runtime_bundle_id": grant_reference["runtime_bundle_id"],
        "api_deployment_id": grant_reference["api_deployment_id"],
        "backend_executor_digest": grant_reference["backend_executor_digest"],
        "activation_id": grant_reference["activation_id"],
        "grant_id_hash": hashlib.sha256(
            str(grant_reference["grant_id"]).encode()
        ).hexdigest(),
        "grant_reference_sha256": hashlib.sha256(
            _canonical(grant_reference)
        ).hexdigest(),
        "provider_task_id_hash": hashlib.sha256(
            provider_task_id.encode()
        ).hexdigest(),
        "provider_task_terminal_status": terminal_status,
        "provider_task_terminal_poll_count": terminal_polls,
        "network_submit_count": 1,
        "provider_fetch_count": provider_fetch_count,
        "callback_recovery": "BOUND_FROM_PROVIDER_CALLBACK",
        "callback_binding_poll_count": callback_polls,
        "callback_attempt_status": str(binding["attempt_status"]),
        "callback_job_status": str(binding["job_status"]),
        "callback_origin_sha256": hashlib.sha256(
            exact_callback_origin.encode()
        ).hexdigest(),
        "callback_url_sha256": hashlib.sha256(callback_url.encode()).hexdigest(),
        "submitter_provider_task_write_count": 0,
        "provider_capabilities_sha256": hashlib.sha256(
            _canonical(capability_document)
        ).hexdigest(),
        "approval_ref": approval,
        "produced_at": current.astimezone(timezone.utc).isoformat(),
    }
    signature = hmac.new(
        signing_key,
        _canonical(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": f"hmac-sha256:{signature}"}


def _database_url(value: object) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("Preview callback database URL is invalid")
    return clean


def _is_fresh_prepared_graph(row: dict[str, Any] | None, reference: dict[str, Any]) -> bool:
    if row is None or row.get("provider_job_id") is not None:
        return False
    expected = {
        "attempt_status": "PREPARED",
        "job_status": "QUEUED",
        "active_attempt_id": reference["attempt_id"],
        "runtime_bundle_id": reference["runtime_bundle_id"],
        "api_deployment_id": reference["api_deployment_id"],
        "order_status": "QUEUED",
    }
    return not any(str(row.get(key) or "") != value for key, value in expected.items())


def _prepare_unknown(database_url: str, reference: dict[str, Any]) -> None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT a.status AS attempt_status, a.provider_job_id,
                       j.status AS job_status, j.active_attempt_id,
                       j.runtime_bundle_id, j.api_deployment_id,
                       o.status AS order_status
                FROM generation_attempts a
                JOIN generation_jobs j ON j.id = a.job_id
                JOIN orders o ON o.id = j.order_id
                WHERE a.id = %s AND a.job_id = %s AND o.id = (
                    SELECT order_id FROM generation_jobs WHERE id = %s
                )
                FOR UPDATE OF a, j, o
                """,
                (
                    reference["attempt_id"],
                    reference["job_id"],
                    reference["job_id"],
                ),
            )
            row = cursor.fetchone()
            if not _is_fresh_prepared_graph(row, reference):
                raise ValueError(
                    "Preview callback recovery graph is not a fresh prepared case"
                )
            cursor.execute(
                """
                UPDATE generation_jobs
                SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'QUEUED'
                  AND active_attempt_id = %s
                RETURNING id
                """,
                (reference["job_id"], reference["attempt_id"]),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback job activation lost its fence")
            cursor.execute(
                """
                UPDATE generation_attempts
                SET status = 'SUBMITTING',
                    submit_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'PREPARED'
                  AND provider_job_id IS NULL
                RETURNING id
                """,
                (reference["attempt_id"],),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback attempt submission transition lost its fence")
            cursor.execute(
                """
                UPDATE orders
                SET status = 'GENERATING', updated_at = CURRENT_TIMESTAMP
                WHERE generation_job_id = %s AND status = 'QUEUED'
                RETURNING id
                """,
                (reference["job_id"],),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback order activation lost its fence")
            cursor.execute(
                """
                UPDATE generation_attempts
                SET status = 'UNKNOWN',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'SUBMITTING'
                  AND provider_job_id IS NULL
                RETURNING id
                """,
                (reference["attempt_id"],),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback attempt unknown transition lost its fence")
            cursor.execute(
                """
                UPDATE generation_jobs
                SET status = 'RECONCILING',
                    next_retry_at = NULL,
                    last_error_code = 'provider_submission_human_required',
                    last_error_detail = 'preview_callback_response_lost_probe',
                    lease_owner = NULL,
                    lease_claim_id = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'ACTIVE'
                  AND active_attempt_id = %s
                RETURNING id
                """,
                (reference["job_id"], reference["attempt_id"]),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback job reconciliation transition lost its fence")
            cursor.execute(
                """
                UPDATE orders
                SET status = 'UNKNOWN_EXTERNAL_STATE',
                    updated_at = CURRENT_TIMESTAMP
                WHERE generation_job_id = %s AND status = 'GENERATING'
                RETURNING id
                """,
                (reference["job_id"],),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback order unknown transition lost its fence")


def _binding_probe(database_url: str, reference: dict[str, Any]) -> dict[str, Any] | None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT a.provider_job_id, a.status AS attempt_status,
                       a.submitted_at, j.status AS job_status,
                       j.lease_owner AS job_lease_owner
                FROM generation_attempts a
                JOIN generation_jobs j ON j.id = a.job_id
                WHERE a.id = %s AND a.job_id = %s
                  AND j.active_attempt_id = a.id
                """,
                (reference["attempt_id"], reference["job_id"]),
            )
            row = cursor.fetchone()
        connection.rollback()
    if row is None:
        return None
    result = dict(row)
    if result.get("submitted_at") is not None:
        result["submitted_at"] = result["submitted_at"].isoformat()
    return result


def _usage_probe(database_url: str, grant_id: str) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    UUID(grant_id)
    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT provider, purpose, runtime_bundle_id,
                       target_api_deployment_id, used_count, last_used_at
                FROM asset_access_grants WHERE id = %s
                """,
                (grant_id,),
            )
            row = cursor.fetchone()
        connection.rollback()
    if row is None:
        raise ValueError("Preview callback grant row is missing")
    result = dict(row)
    if result.get("last_used_at") is not None:
        result["last_used_at"] = result["last_used_at"].isoformat()
    return result


def _revoke_grant(database_url: str, reference: dict[str, Any]) -> None:
    import psycopg2

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE asset_access_grants
                SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                WHERE id = %s AND provider = 'evolink'
                  AND purpose = 'generation-input'
                  AND asset_id = %s AND job_id = %s AND attempt_id = %s
                  AND runtime_bundle_id = %s
                  AND target_api_deployment_id = %s
                RETURNING id
                """,
                (
                    reference["grant_id"],
                    reference["asset_id"],
                    reference["job_id"],
                    reference["attempt_id"],
                    reference["runtime_bundle_id"],
                    reference["api_deployment_id"],
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("Preview callback grant revocation mismatch")


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip().lower()


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
    parser.add_argument("--write-database-url-env", required=True)
    parser.add_argument("--read-database-url-env", required=True)
    parser.add_argument("--api-key-env", default="EVOLINK_API_KEY")
    parser.add_argument("--api-base-url-env", default="EVOLINK_API_BASE_URL")
    parser.add_argument("--image-model-env", default="EVOLINK_IMAGE_MODEL")
    parser.add_argument(
        "--callback-origin-env",
        default="EVOLINK_CALLBACK_BASE_URL",
    )
    parser.add_argument("--callback-secret-env", default="PREVIEW_SECRET_KEY")
    parser.add_argument("--approval-id-env", default="PREVIEW_COMMERCIAL_APPROVAL_ID")
    parser.add_argument("--signing-key-env", default="PROVIDER_EVIDENCE_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = args.expected_source_sha.strip().lower()
        if _git_head() != source:
            raise ValueError("callback recovery source SHA is not the current checkout")
        capabilities = _load_object(args.capabilities)
        reference = _load_object(args.grant_reference)
        write_database_url = os.environ.get(args.write_database_url_env, "")
        read_database_url = os.environ.get(args.read_database_url_env, "")
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            try:
                report = verify_callback_recovery(
                    capability_document=capabilities,
                    grant_reference=reference,
                    expected_source_sha=source,
                    api_key=os.environ.get(args.api_key_env, ""),
                    api_base_url=os.environ.get(args.api_base_url_env, ""),
                    image_model=os.environ.get(args.image_model_env, ""),
                    callback_origin=os.environ.get(args.callback_origin_env, ""),
                    callback_secret=os.environ.get(args.callback_secret_env, ""),
                    approval_ref=os.environ.get(args.approval_id_env, ""),
                    signing_key=os.environ.get(args.signing_key_env, "").encode(),
                    client=client,
                    prepare_unknown=lambda: _prepare_unknown(
                        write_database_url,
                        reference,
                    ),
                    usage_probe=lambda grant_id: _usage_probe(
                        read_database_url,
                        grant_id,
                    ),
                    binding_probe=lambda: _binding_probe(
                        read_database_url,
                        reference,
                    ),
                )
            finally:
                _revoke_grant(write_database_url, reference)
        _write_create_once(Path(args.output), report)
        print(
            json.dumps(
                {
                    "passed": True,
                    "report_sha256": hashlib.sha256(
                        _canonical(report)
                    ).hexdigest(),
                    "callback_recovery": report["callback_recovery"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        httpx.HTTPError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
