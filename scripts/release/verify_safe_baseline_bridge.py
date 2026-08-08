#!/usr/bin/env python3
"""Verify the exact safe-baseline-to-0021 bridge after the protected migration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any


SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
TARGET_REVISION = "20260710_0021"
REQUIRED_TABLES = (
    "release_activations",
    "release_phase_evidence",
    "ops_feature_flags",
    "payment_events",
    "release_observation_runs",
    "release_observation_samples",
    "release_observation_recoveries",
)


def _database_url(value: str) -> str:
    url = str(value or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("bridge database URL is invalid")
    return url


def build_bridge_report(
    *,
    source_sha: str,
    revision_rows: list[dict[str, Any]],
    release_rows: list[dict[str, Any]],
    safe_baseline_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    if not SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("bridge source SHA is invalid")
    if revision_rows != [{"version_num": TARGET_REVISION}]:
        raise ValueError("Production schema is not exactly 20260710_0021")
    if len(release_rows) != 1:
        raise ValueError("exactly one sealed COMMERCIAL_7A release is required")
    release = release_rows[0]
    if (
        release.get("source_sha") != source_sha
        or release.get("kind") != "COMMERCIAL_7A"
        or release.get("environment") != "production"
        or release.get("phase") != "MANIFEST_SEALED"
        or not release.get("runtime_bundle_id")
        or not release.get("manifest_sha256")
    ):
        raise ValueError("sealed COMMERCIAL_7A bridge coordinates are invalid")
    if len(safe_baseline_rows) != 1:
        raise ValueError("exactly one completed safe baseline is required")
    baseline = safe_baseline_rows[0]
    if (
        baseline.get("environment") != "production"
        or baseline.get("kind") != "SAFE_BASELINE_INSTALL"
        or baseline.get("phase") != "COMPLETED"
        or not baseline.get("api_deployment_id")
        or not baseline.get("runtime_bundle_id")
    ):
        raise ValueError("safe baseline coordinates are incomplete")
    present = {str(row.get("table_name")) for row in table_rows if row.get("present")}
    missing = sorted(set(REQUIRED_TABLES) - present)
    if missing:
        raise ValueError(f"0021 bridge tables are missing: {', '.join(missing)}")
    return {
        "schema": "vowpic.safe-baseline-bridge.v1",
        "passed": True,
        "source_sha": source_sha,
        "target_revision": TARGET_REVISION,
        "release_activation_id": str(release["id"]),
        "runtime_bundle_id": release["runtime_bundle_id"],
        "manifest_sha256": release["manifest_sha256"],
        "safe_baseline_activation_id": str(baseline["id"]),
        "safe_baseline_deployment_id": baseline["api_deployment_id"],
        "safe_baseline_runtime_bundle_id": baseline["runtime_bundle_id"],
        "required_table_count": len(REQUIRED_TABLES),
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
    }


def read_bridge_state(database_url: str, *, source_sha: str) -> tuple[list[dict[str, Any]], ...]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_database_url(database_url)) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT id, environment, kind, source_sha, runtime_bundle_id,
                       manifest_sha256, phase
                FROM release_activations
                WHERE environment = 'production' AND kind = 'COMMERCIAL_7A'
                  AND source_sha = %s AND phase = 'MANIFEST_SEALED'
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (source_sha,),
            )
            releases = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT id, environment, kind, runtime_bundle_id,
                       api_deployment_id, phase
                FROM release_activations
                WHERE environment = 'production'
                  AND kind = 'SAFE_BASELINE_INSTALL' AND phase = 'COMPLETED'
                ORDER BY updated_at DESC
                LIMIT 2
                """
            )
            baselines = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT required.table_name,
                       to_regclass('public.' || required.table_name) IS NOT NULL AS present
                FROM unnest(%s::text[]) AS required(table_name)
                ORDER BY required.table_name
                """,
                (list(REQUIRED_TABLES),),
            )
            tables = [dict(row) for row in cursor.fetchall()]
    return revisions, releases, baselines, tables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source_sha = str(args.source_sha or "").strip().lower()
        state = read_bridge_state(
            os.environ.get(args.database_url_env, ""), source_sha=source_sha
        )
        report = build_bridge_report(
            source_sha=source_sha,
            revision_rows=state[0],
            release_rows=state[1],
            safe_baseline_rows=state[2],
            table_rows=state[3],
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
