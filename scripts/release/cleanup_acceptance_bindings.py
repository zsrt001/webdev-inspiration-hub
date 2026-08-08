#!/usr/bin/env python3
"""Revoke every unused Production acceptance binding for one staged deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


_DEPLOYMENT_ID = re.compile(r"^dpl_[A-Za-z0-9_-]{3,156}$")
ACTIVATION_KINDS = ("COMMERCIAL_7A", "GOOGLE_AUTH_ONLY")


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    if not clean.startswith(("postgresql://", "postgres://")):
        raise ValueError("acceptance binding cleanup database URL is invalid")
    return clean


def validate_activation(
    activation: dict[str, Any], *, deployment_id: str, approval: str,
    kind: str = "COMMERCIAL_7A",
) -> None:
    expected = {
        "environment": "production",
        "kind": kind,
        "api_deployment_id": deployment_id,
        "approval": approval,
    }
    if any(str(activation.get(key)) != str(value) for key, value in expected.items()):
        raise ValueError("acceptance binding cleanup activation coordinates are invalid")
    allowed_phases = {
        "ACCEPTANCE_READY",
        "TARGET_ACCEPTED",
        "TARGET_PROMOTED",
        "PUBLIC_INVALIDATED",
        "ACTIVATED",
        "OBSERVING",
        "7A_ACCEPTED",
    }
    if kind == "GOOGLE_AUTH_ONLY":
        allowed_phases = {"ACCEPTANCE_READY"}
    if activation.get("phase") not in allowed_phases:
        raise ValueError("acceptance binding cleanup activation phase is invalid")


def build_cleanup_report(
    *,
    activation: dict[str, Any],
    deployment_id: str,
    before: dict[str, int],
    revoked_now: int,
    after: dict[str, int],
    require_zero_unused: bool,
    completed_at: datetime | None = None,
) -> dict[str, Any]:
    for label, counts in (("before", before), ("after", after)):
        if set(counts) != {
            "total",
            "consumed",
            "revoked",
            "unused_unrevoked",
            "active_unused",
            "expired_unused",
        } or any(not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ValueError(f"acceptance binding cleanup {label} counts are invalid")
    if revoked_now < 0 or revoked_now > before["unused_unrevoked"]:
        raise ValueError("acceptance binding cleanup revoked count is invalid")
    zero_unused = after["unused_unrevoked"] == 0 and after["active_unused"] == 0
    if require_zero_unused and not zero_unused:
        raise ValueError("unused acceptance bindings remain after cleanup")
    current = completed_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("acceptance binding cleanup timestamp must be timezone-aware")
    return {
        "schema": "vowpic.acceptance-binding-cleanup.v1",
        "passed": zero_unused,
        "activation_id": str(activation["id"]),
        "source_sha": activation["source_sha"],
        "runtime_bundle_id": activation["runtime_bundle_id"],
        "deployment_id": deployment_id,
        "before": before,
        "revoked_now": revoked_now,
        "after": after,
        "zero_unused": zero_unused,
        "completed_at": current.astimezone(timezone.utc).isoformat(),
    }


def _binding_counts(cursor: Any, *, deployment_id: str) -> dict[str, int]:
    cursor.execute(
        """
        SELECT
          COUNT(*)::integer AS total,
          COUNT(*) FILTER (WHERE consumed_at IS NOT NULL)::integer AS consumed,
          COUNT(*) FILTER (WHERE revoked_at IS NOT NULL)::integer AS revoked,
          COUNT(*) FILTER (
            WHERE consumed_at IS NULL AND revoked_at IS NULL
          )::integer AS unused_unrevoked,
          COUNT(*) FILTER (
            WHERE consumed_at IS NULL AND revoked_at IS NULL
              AND expires_at > CURRENT_TIMESTAMP
          )::integer AS active_unused,
          COUNT(*) FILTER (
            WHERE consumed_at IS NULL AND revoked_at IS NULL
              AND expires_at <= CURRENT_TIMESTAMP
          )::integer AS expired_unused
        FROM acceptance_identity_bindings
        WHERE environment = 'production' AND deployment_id = %s
        """,
        (deployment_id,),
    )
    return dict(cursor.fetchone())


def cleanup_bindings(
    database_url: str,
    *,
    deployment_id: str,
    approval: str,
    kind: str = "COMMERCIAL_7A",
    require_zero_unused: bool,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"vowpic-acceptance-binding-cleanup:{deployment_id}",),
            )
            cursor.execute(
                """
                SELECT * FROM release_activations
                WHERE environment = 'production' AND kind = %s
                  AND api_deployment_id = %s
                ORDER BY updated_at DESC LIMIT 2
                FOR UPDATE
                """,
                (kind, deployment_id),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            if len(rows) != 1:
                raise ValueError("exactly one staged Production activation is required")
            activation = rows[0]
            validate_activation(
                activation, deployment_id=deployment_id, approval=approval, kind=kind
            )
            cursor.execute(
                """
                SELECT id FROM acceptance_identity_bindings
                WHERE environment = 'production' AND deployment_id = %s
                FOR UPDATE
                """,
                (deployment_id,),
            )
            cursor.fetchall()
            before = _binding_counts(cursor, deployment_id=deployment_id)
            cursor.execute(
                """
                UPDATE acceptance_identity_bindings
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE environment = 'production' AND deployment_id = %s
                  AND consumed_at IS NULL AND consumed_user_id IS NULL
                  AND revoked_at IS NULL
                """,
                (deployment_id,),
            )
            revoked_now = int(cursor.rowcount)
            after = _binding_counts(cursor, deployment_id=deployment_id)
            return build_cleanup_report(
                activation=activation,
                deployment_id=deployment_id,
                before=before,
                revoked_now=revoked_now,
                after=after,
                require_zero_unused=require_zero_unused,
            )


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--kind", choices=ACTIVATION_KINDS, default="COMMERCIAL_7A")
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--approval-id-env", default="PRODUCTION_ACCEPTANCE_APPROVAL_ID")
    parser.add_argument("--require-zero-unused", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        deployment_id = str(args.deployment_id or "").strip()
        if not _DEPLOYMENT_ID.fullmatch(deployment_id):
            raise ValueError("acceptance binding cleanup deployment ID is invalid")
        approval = os.environ.get(args.approval_id_env, "").strip()
        if not approval or len(approval) > 160:
            raise ValueError("acceptance binding cleanup approval is required")
        report = cleanup_bindings(
            os.environ.get(args.database_url_env, ""),
            deployment_id=deployment_id,
            approval=approval,
            kind=args.kind,
            require_zero_unused=args.require_zero_unused,
        )
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
