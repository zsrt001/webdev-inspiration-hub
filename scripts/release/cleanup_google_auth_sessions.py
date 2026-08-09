#!/usr/bin/env python3
"""Revoke every browser session issued by one GOOGLE_AUTH_ONLY acceptance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")
PRODUCTION_ACTIVATION_FENCE = "vowpic-production-capability-activation"


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("Google acceptance session cleanup database URL is invalid")
    return clean


def build_cleanup_report(
    *,
    deployment_id: str,
    before_total: int,
    before_unrevoked: int,
    revoked_now: int,
    after_unrevoked: int,
    completed_at: datetime | None = None,
) -> dict[str, Any]:
    counts = (before_total, before_unrevoked, revoked_now, after_unrevoked)
    if any(not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("Google acceptance session cleanup counts are invalid")
    if before_unrevoked > before_total or revoked_now > before_unrevoked:
        raise ValueError("Google acceptance session cleanup count ordering is invalid")
    current = completed_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Google acceptance session cleanup timestamp must be timezone-aware")
    return {
        "schema": "vowpic.google-auth-session-cleanup.v1",
        "passed": after_unrevoked == 0,
        "deployment_id": deployment_id,
        "before_total": before_total,
        "before_unrevoked": before_unrevoked,
        "revoked_now": revoked_now,
        "after_unrevoked": after_unrevoked,
        "completed_at": current.astimezone(timezone.utc).isoformat(),
    }


def _session_counts(cursor: Any, *, deployment_id: str) -> tuple[int, int]:
    cursor.execute(
        """
        SELECT COUNT(*)::integer AS total,
               COUNT(*) FILTER (WHERE session.revoked_at IS NULL)::integer AS unrevoked
        FROM auth_sessions AS session
        JOIN acceptance_identity_bindings AS binding
          ON binding.id = session.acceptance_binding_id
        WHERE binding.environment = 'production'
          AND binding.deployment_id = %s
        """,
        (deployment_id,),
    )
    row = cursor.fetchone()
    return int(row["total"]), int(row["unrevoked"])


def cleanup_sessions(
    database_url: str,
    *,
    deployment_id: str,
    approval: str,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (PRODUCTION_ACTIVATION_FENCE,),
            )
            cursor.execute(
                """
                SELECT id
                FROM release_activations
                WHERE environment = 'production' AND kind = 'GOOGLE_AUTH_ONLY'
                  AND api_deployment_id = %s AND approval = %s
                  AND phase = 'ACCEPTANCE_READY'
                ORDER BY updated_at DESC LIMIT 2
                FOR UPDATE
                """,
                (deployment_id, approval),
            )
            activations = cursor.fetchall()
            if len(activations) != 1:
                raise ValueError("exact GOOGLE_AUTH_ONLY activation is unavailable for cleanup")
            before_total, before_unrevoked = _session_counts(
                cursor, deployment_id=deployment_id
            )
            cursor.execute(
                """
                SELECT session.id
                FROM auth_sessions AS session
                JOIN acceptance_identity_bindings AS binding
                  ON binding.id = session.acceptance_binding_id
                WHERE binding.environment = 'production'
                  AND binding.deployment_id = %s
                ORDER BY session.id
                FOR UPDATE OF session
                """,
                (deployment_id,),
            )
            session_ids = [str(row["id"]) for row in cursor.fetchall()]
            if session_ids:
                cursor.execute(
                    """
                    SELECT id FROM auth_refresh_tokens
                    WHERE session_id = ANY(%s::uuid[])
                    ORDER BY session_id, generation
                    FOR UPDATE
                    """,
                    (session_ids,),
                )
                cursor.fetchall()
                cursor.execute(
                    """
                    UPDATE auth_refresh_tokens
                    SET status = 'REVOKED', revoked_at = CURRENT_TIMESTAMP
                    WHERE session_id = ANY(%s::uuid[])
                      AND status <> 'REVOKED'
                    """,
                    (session_ids,),
                )
            cursor.execute(
                """
                UPDATE auth_sessions AS session
                SET revoked_at = CURRENT_TIMESTAMP,
                    token_version = token_version + 1
                FROM acceptance_identity_bindings AS binding
                WHERE binding.id = session.acceptance_binding_id
                  AND binding.environment = 'production'
                  AND binding.deployment_id = %s
                  AND session.revoked_at IS NULL
                """,
                (deployment_id,),
            )
            revoked_now = int(cursor.rowcount)
            _, after_unrevoked = _session_counts(cursor, deployment_id=deployment_id)
            return build_cleanup_report(
                deployment_id=deployment_id,
                before_total=before_total,
                before_unrevoked=before_unrevoked,
                revoked_now=revoked_now,
                after_unrevoked=after_unrevoked,
            )


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        deployment_id = str(args.deployment_id or "").strip()
        if not DEPLOYMENT_ID.fullmatch(deployment_id):
            raise ValueError("Google acceptance session cleanup deployment ID is invalid")
        database_url = os.environ.get(args.database_url_env, "").strip()
        approval = os.environ.get(args.approval_id_env, "").strip()
        if not approval or len(approval) > 160:
            raise ValueError("Google acceptance session cleanup approval is required")
        report = cleanup_sessions(
            database_url,
            deployment_id=deployment_id,
            approval=approval,
        )
        _write_create_once(Path(args.output), report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Google acceptance session cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
