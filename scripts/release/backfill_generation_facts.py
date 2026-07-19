#!/usr/bin/env python3
"""Classify legacy generation evidence without inventing runtime coordinates."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from scripts.release._migration_common import (
    add_common_arguments,
    load_invocation,
    run_batch_migration,
    safe_main,
    write_report_create_once,
)


_TERMINAL = {"COMPLETED", "READY", "FAILED", "CANCELLED", "DELETED"}
_SELECT = """
WITH unstamped AS (
  SELECT
    'order:' || o.id::text AS id,
    o.id AS order_id,
    NULL::uuid AS generation_job_id,
    upper(o.status) AS order_status,
    'legacy_task_without_job'::text AS reason
  FROM orders o
  WHERE o.task_id IS NOT NULL
    AND o.generation_job_id IS NULL
  UNION ALL
  SELECT
    'job:' || gj.id::text,
    gj.order_id,
    gj.id,
    upper(o.status),
    'job_runtime_stamp_invalid'
  FROM generation_jobs gj
  JOIN orders o ON o.id = gj.order_id
  WHERE gj.payload_version <> 'generation-job.v1'
     OR btrim(gj.api_deployment_id) = ''
     OR btrim(gj.runtime_bundle_id) = ''
     OR btrim(gj.expected_worker_image_digest) = ''
)
SELECT *
FROM unstamped
WHERE (:after IS NULL OR id > :after)
ORDER BY id
LIMIT :batch_size
"""


def classify_generation(order_status: str) -> str:
    return (
        "legacy_terminal_evidence"
        if str(order_status).upper() in _TERMINAL
        else "quarantined_runnable"
    )


async def _process_batch(db, rows, invocation):
    counts = {
        "legacy_terminal_evidence": 0,
        "quarantined_runnable": 0,
        "invented_runtime_stamps": 0,
    }
    blockers: dict[str, int] = {}
    for row in rows:
        disposition = classify_generation(str(row["order_status"]))
        counts[disposition] += 1
        if disposition == "quarantined_runnable" and not invocation.write:
            blockers["unstamped_runnable_generation"] = (
                blockers.get("unstamped_runnable_generation", 0) + 1
            )
        if not invocation.write:
            continue
        if disposition == "legacy_terminal_evidence":
            await db.execute(
                text(
                    """
                    UPDATE orders
                    SET delivery_status = CASE
                      WHEN delivery_status = 'PENDING' THEN 'LEGACY_EVIDENCE'
                      ELSE delivery_status
                    END
                    WHERE id = :order_id
                    """
                ),
                {"order_id": row["order_id"]},
            )
            if row["generation_job_id"] is not None:
                await db.execute(
                    text(
                        """
                        UPDATE generation_jobs
                        SET status = CASE
                              WHEN status IN ('QUEUED','ACTIVE','RECONCILING')
                              THEN 'CANCELLED'
                              ELSE status
                            END,
                            payload_version = 'legacy-evidence.v1',
                            delivery_status = 'LEGACY_EVIDENCE',
                            last_error_code = COALESCE(
                              last_error_code,
                              'LEGACY_RUNTIME_UNPROVEN'
                            ),
                            next_retry_at = NULL,
                            lease_owner = NULL,
                            lease_claim_id = NULL,
                            lease_expires_at = NULL,
                            heartbeat_at = NULL,
                            finished_at = COALESCE(
                              finished_at,
                              CURRENT_TIMESTAMP
                            )
                        WHERE id = :generation_job_id
                        """
                    ),
                    {"generation_job_id": row["generation_job_id"]},
                )
        else:
            await db.execute(
                text(
                    """
                    UPDATE orders
                    SET status = 'UNKNOWN_EXTERNAL_STATE',
                        delivery_status = 'QUARANTINED'
                    WHERE id = :order_id
                      AND upper(status) NOT IN (
                        'COMPLETED','READY','FAILED','CANCELLED','DELETED'
                      )
                    """
                ),
                {"order_id": row["order_id"]},
            )
            if row["generation_job_id"] is not None:
                await db.execute(
                    text(
                        """
                        UPDATE generation_jobs
                        SET status = 'CANCELLED',
                            payload_version = 'legacy-quarantined.v1',
                            delivery_status = 'QUARANTINED',
                            last_error_code = 'LEGACY_RUNTIME_UNPROVEN',
                            next_retry_at = NULL,
                            lease_owner = NULL,
                            lease_claim_id = NULL,
                            lease_expires_at = NULL,
                            heartbeat_at = NULL,
                            finished_at = COALESCE(
                              finished_at,
                              CURRENT_TIMESTAMP
                            )
                        WHERE id = :generation_job_id
                        """
                    ),
                    {"generation_job_id": row["generation_job_id"]},
                )
    return counts, blockers


async def _run(args: argparse.Namespace) -> None:
    invocation = load_invocation(args, script_path=Path(__file__))
    counts, blockers = await run_batch_migration(
        invocation,
        select_sql=_SELECT,
        process_batch=_process_batch,
    )
    if invocation.write and counts["quarantined_runnable"]:
        blockers["unstamped_runnable_generation"] = counts[
            "quarantined_runnable"
        ]
    write_report_create_once(
        invocation,
        tool="backfill_generation_facts",
        counts=counts,
        blockers=blockers,
        passed=not blockers,
        extra={
            "generation_can_open": counts["quarantined_runnable"] == 0,
            "invented_runtime_stamps": 0,
        },
    )
    if blockers:
        raise ValueError("unstamped runnable generation remains quarantined")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    args = parser.parse_args()
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
