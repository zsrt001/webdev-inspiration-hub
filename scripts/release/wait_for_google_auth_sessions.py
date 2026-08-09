#!/usr/bin/env python3
"""Wait for two interactive, deployment-bound Production Google sessions."""

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


DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_CAPABILITIES = {
    "google_auth",
    "authenticated_upload",
    "generation",
    "credit_pack_checkout",
    "subscription_billing",
    "private_download",
    "partner_invite",
}


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("interactive Google acceptance database URL is invalid")
    return clean


def _coordinate_hmac(key: str, label: str, value: str) -> str:
    return hmac.new(
        key.encode("utf-8"),
        f"{label}:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_capability_boundary(
    rows: list[dict[str, Any]],
    *,
    deployment_id: str,
    activation_id: str,
) -> None:
    if len(rows) != len(EXPECTED_CAPABILITIES):
        raise ValueError("interactive Google acceptance capability inventory is incomplete")
    by_name = {str(row["capability"]): row for row in rows}
    if set(by_name) != EXPECTED_CAPABILITIES:
        raise ValueError("interactive Google acceptance capability inventory is invalid")
    for capability, row in by_name.items():
        if capability == "google_auth":
            expected = {
                "state": "ACCEPTANCE_COHORT",
                "deployment_id": deployment_id,
                "release_activation_id": activation_id,
            }
            if any(str(row.get(key)) != value for key, value in expected.items()):
                raise ValueError("Google auth is outside the exact interactive acceptance cohort")
            if row.get("expires_at") is None:
                raise ValueError("interactive Google acceptance cohort has no expiry")
        elif (
            row.get("state") != "OFF"
            or row.get("deployment_id") is not None
            or row.get("release_activation_id") is not None
            or row.get("expires_at") is not None
        ):
            raise ValueError("a non-Google capability became available during acceptance")


def build_acceptance_report(
    bindings: list[dict[str, Any]],
    *,
    source_sha: str,
    deployment_id: str,
    activation_id: str,
    hmac_key: str,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    if len(bindings) != 2:
        raise ValueError("interactive Google acceptance requires exactly two bindings")
    if any(
        row.get("provider") != "google"
        or row.get("revoked_at") is not None
        or not row.get("binding_id")
        for row in bindings
    ):
        raise ValueError("interactive Google acceptance binding coordinates are invalid")
    consumed = [row for row in bindings if row.get("consumed_at") is not None]
    if any(
        (row.get("consumed_at") is None) != (row.get("consumed_user_id") is None)
        for row in bindings
    ):
        raise ValueError("interactive Google acceptance binding consumption is incomplete")
    if len(consumed) != 2:
        return None
    user_ids = {str(row["consumed_user_id"]) for row in consumed}
    if len(user_ids) != 2:
        raise ValueError("interactive Google acceptance did not use two distinct users")
    if any(
        not row.get("session_id")
        or str(row.get("session_user_id")) != str(row["consumed_user_id"])
        for row in consumed
    ):
        raise ValueError("interactive Google acceptance has no linked browser session")
    current = observed_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("interactive Google acceptance timestamp must be timezone-aware")
    return {
        "schema": "vowpic.google-auth-interactive-acceptance.v1",
        "passed": True,
        "source_sha": source_sha,
        "deployment_id": deployment_id,
        "activation_id": activation_id,
        "consumed_bindings": 2,
        "distinct_users": 2,
        "linked_sessions": 2,
        "binding_id_hmac_sha256": sorted(
            _coordinate_hmac(hmac_key, "binding", str(row["binding_id"]))
            for row in consumed
        ),
        "user_id_hmac_sha256": sorted(
            _coordinate_hmac(hmac_key, "user", str(row["consumed_user_id"]))
            for row in consumed
        ),
        "session_id_hmac_sha256": sorted(
            _coordinate_hmac(hmac_key, "session", str(row["session_id"]))
            for row in consumed
        ),
        "observed_at": current.astimezone(timezone.utc).isoformat(),
    }


def _snapshot(
    cursor: Any,
    *,
    source_sha: str,
    deployment_id: str,
    approval: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT id::text AS id
        FROM release_activations
        WHERE environment = 'production' AND kind = 'GOOGLE_AUTH_ONLY'
          AND source_sha = %s AND api_deployment_id = %s
          AND approval = %s AND phase = 'ACCEPTANCE_READY'
        ORDER BY updated_at DESC LIMIT 2
        """,
        (source_sha, deployment_id, approval),
    )
    activations = [dict(row) for row in cursor.fetchall()]
    if len(activations) != 1:
        raise ValueError("exact interactive GOOGLE_AUTH_ONLY activation is unavailable")
    activation_id = str(activations[0]["id"])
    cursor.execute(
        """
        SELECT capability, state, deployment_id,
               release_activation_id::text AS release_activation_id, expires_at
        FROM ops_feature_flags
        WHERE environment = 'production'
        ORDER BY capability
        """
    )
    flags = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT binding.id::text AS binding_id, binding.provider,
               binding.consumed_user_id::text AS consumed_user_id,
               binding.consumed_at, binding.revoked_at,
               session.id::text AS session_id,
               session.user_id::text AS session_user_id
        FROM acceptance_identity_bindings AS binding
        LEFT JOIN auth_sessions AS session
          ON session.acceptance_binding_id = binding.id
        WHERE binding.environment = 'production'
          AND binding.deployment_id = %s
        ORDER BY binding.subject_hmac
        """,
        (deployment_id,),
    )
    return activation_id, flags, [dict(row) for row in cursor.fetchall()]


def wait_for_sessions(
    database_url: str,
    *,
    source_sha: str,
    deployment_id: str,
    approval: str,
    hmac_key: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    deadline = time.monotonic() + timeout_seconds
    last_consumed = -1
    while True:
        with psycopg2.connect(_database_url(database_url)) as connection:
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                activation_id, flags, bindings = _snapshot(
                    cursor,
                    source_sha=source_sha,
                    deployment_id=deployment_id,
                    approval=approval,
                )
        validate_capability_boundary(
            flags,
            deployment_id=deployment_id,
            activation_id=activation_id,
        )
        report = build_acceptance_report(
            bindings,
            source_sha=source_sha,
            deployment_id=deployment_id,
            activation_id=activation_id,
            hmac_key=hmac_key,
        )
        if report is not None:
            return report
        consumed = sum(row.get("consumed_at") is not None for row in bindings)
        if consumed != last_consumed:
            print(
                json.dumps(
                    {"status": "waiting_for_google_sessions", "consumed": consumed, "required": 2},
                    sort_keys=True,
                ),
                flush=True,
            )
            last_consumed = consumed
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"interactive Google acceptance timed out with {consumed} of 2 sessions"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--database-url-env", default="PRODUCTION_READ_ONLY_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--hmac-key-env", default="ACCEPTANCE_EVIDENCE_SIGNING_KEY")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=int, default=2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source_sha = str(args.source_sha or "").strip().lower()
        deployment_id = str(args.deployment_id or "").strip()
        if not SOURCE_SHA.fullmatch(source_sha):
            raise ValueError("interactive Google acceptance source SHA is invalid")
        if not DEPLOYMENT_ID.fullmatch(deployment_id):
            raise ValueError("interactive Google acceptance deployment ID is invalid")
        if not 60 <= args.timeout_seconds <= 1800:
            raise ValueError("interactive Google acceptance timeout must be 60..1800 seconds")
        if not 1 <= args.poll_seconds <= 30:
            raise ValueError("interactive Google acceptance poll must be 1..30 seconds")
        approval = os.environ.get(args.approval_id_env, "").strip()
        hmac_key = os.environ.get(args.hmac_key_env, "").strip()
        database_url = os.environ.get(args.database_url_env, "").strip()
        if not approval or len(approval) > 160:
            raise ValueError("interactive Google acceptance approval is required")
        if len(hmac_key) < 32:
            raise ValueError("interactive Google acceptance HMAC key is invalid")
        report = wait_for_sessions(
            database_url,
            source_sha=source_sha,
            deployment_id=deployment_id,
            approval=approval,
            hmac_key=hmac_key,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        _write_create_once(Path(args.output), report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"interactive Google acceptance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
