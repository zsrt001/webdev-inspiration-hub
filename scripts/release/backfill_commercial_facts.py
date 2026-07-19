#!/usr/bin/env python3
"""Backfill only provable legacy credit lineage and explicit unverified facts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path
import sys
from typing import Any
from uuid import UUID, uuid5

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


_FACT_NAMESPACE = UUID("57a63316-2de7-5d69-83d6-96a484d52de2")
_SELECT = """
WITH positive_roots AS (
  SELECT
    tx.id AS source_id,
    tx.user_id,
    tx.amount,
    tx.created_at,
    COALESCE(uc.balance, 0)::bigint AS materialized_balance,
    sum(tx.amount) OVER (PARTITION BY tx.user_id)::bigint AS total_positive,
    COALESCE(
      sum(tx.amount) OVER (
        PARTITION BY tx.user_id
        ORDER BY tx.created_at, tx.id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ),
      0
    )::bigint AS positive_before,
    (
      SELECT COALESCE(sum(all_tx.amount), 0)::bigint
      FROM credit_transactions all_tx
      WHERE all_tx.user_id = tx.user_id
    ) AS ledger_balance
  FROM credit_transactions tx
  JOIN user_credits uc ON uc.user_id = tx.user_id
  LEFT JOIN credit_grant_lots lot ON lot.root_transaction_id = tx.id
  WHERE tx.amount > 0 AND lot.id IS NULL
), facts AS (
  SELECT
    'grant:' || source_id::text AS id,
    'legacy_pool'::text AS fact_kind,
    source_id,
    user_id,
    amount::bigint AS amount,
    LEAST(
      amount,
      GREATEST(
        0,
        total_positive - GREATEST(materialized_balance, 0) - positive_before
      )
    )::bigint AS consumed_amount,
    materialized_balance,
    ledger_balance
  FROM positive_roots
  UNION ALL
  SELECT
    'debit:' || tx.id::text,
    'legacy_unlinked',
    tx.id,
    tx.user_id,
    abs(tx.amount)::bigint,
    0::bigint,
    COALESCE(uc.balance, 0)::bigint,
    (
      SELECT COALESCE(sum(all_tx.amount), 0)::bigint
      FROM credit_transactions all_tx
      WHERE all_tx.user_id = tx.user_id
    )
  FROM credit_transactions tx
  JOIN user_credits uc ON uc.user_id = tx.user_id
  WHERE tx.amount < 0 AND (tx.source IS NULL OR tx.source_id IS NULL)
  UNION ALL
  SELECT
    'order:' || o.id::text,
    'legacy_unverified',
    o.id,
    o.user_id,
    0::bigint,
    0::bigint,
    0::bigint,
    0::bigint
  FROM orders o
  WHERE upper(o.status) = 'COMPLETED'
    AND NOT EXISTS (SELECT 1 FROM qa_verdicts q WHERE q.job_id = o.generation_job_id)
)
SELECT *
FROM facts
WHERE (:after IS NULL OR id > :after)
ORDER BY id
LIMIT :batch_size
"""


def _case_hash(kind: str, source_id: object) -> str:
    return hashlib.sha256(
        f"vowpic:{kind}:{source_id}".encode("utf-8")
    ).hexdigest()


async def _process_batch(db, rows, invocation):
    counts: dict[str, int] = {
        "legacy_pool": 0,
        "legacy_unlinked": 0,
        "legacy_unverified": 0,
    }
    blockers: dict[str, int] = {}
    for row in rows:
        kind = str(row["fact_kind"])
        counts[kind] += 1
        if kind != "legacy_unverified" and int(row["ledger_balance"]) != int(
            row["materialized_balance"]
        ):
            blockers["ledger_balance_mismatch"] = (
                blockers.get("ledger_balance_mismatch", 0) + 1
            )
            continue
        if not invocation.write:
            continue
        source_id = UUID(str(row["source_id"]))
        user_id = UUID(str(row["user_id"]))
        if kind == "legacy_pool":
            original = int(row["amount"])
            consumed = int(row["consumed_amount"])
            if original <= 0 or consumed < 0 or consumed > original:
                raise ValueError("legacy pool allocation is invalid")
            await db.execute(
                text(
                    """
                    INSERT INTO credit_grant_lots (
                      id, user_id, root_transaction_id, source_type, source_id,
                      original_amount, debt_offset_amount, reversed_amount,
                      frozen_amount, consumed_amount, retention_tier
                    ) VALUES (
                      :id, :user_id, :root_transaction_id, 'LEGACY_POOL',
                      :source_id, :original_amount, 0, 0, 0, :consumed_amount,
                      'legacy'
                    )
                    ON CONFLICT (root_transaction_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid5(_FACT_NAMESPACE, f"legacy-pool:{source_id}"),
                    "user_id": user_id,
                    "root_transaction_id": source_id,
                    "source_id": f"legacy_pool:{source_id}",
                    "original_amount": original,
                    "consumed_amount": consumed,
                },
            )
        elif kind == "legacy_unlinked":
            case_hash = _case_hash(kind, source_id)
            await db.execute(
                text(
                    """
                    INSERT INTO payment_reconciliation_cases (
                      id, user_id, provider, case_key, subject_type, subject_id,
                      reason_code, status, raw_payload_sha256, attempt_count
                    ) VALUES (
                      :id, :user_id, 'legacy', :case_key, 'credit_transaction',
                      :subject_id, 'LEGACY_UNLINKED_DEBIT', 'OPEN',
                      :payload_sha256, 0
                    )
                    ON CONFLICT (provider, case_key) DO NOTHING
                    """
                ),
                {
                    "id": uuid5(_FACT_NAMESPACE, f"legacy-debit:{source_id}"),
                    "user_id": user_id,
                    "case_key": case_hash,
                    "subject_id": str(source_id),
                    "payload_sha256": case_hash,
                },
            )
        else:
            await db.execute(
                text(
                    """
                    UPDATE orders
                    SET delivery_status = 'LEGACY_UNVERIFIED'
                    WHERE id = :order_id
                      AND upper(status) = 'COMPLETED'
                      AND NOT EXISTS (
                        SELECT 1 FROM qa_verdicts q
                        WHERE q.job_id = orders.generation_job_id
                      )
                    """
                ),
                {"order_id": source_id},
            )
    return counts, blockers


async def _run(args: argparse.Namespace) -> None:
    invocation = load_invocation(args, script_path=Path(__file__))
    counts, blockers = await run_batch_migration(
        invocation,
        select_sql=_SELECT,
        process_batch=_process_batch,
    )
    write_report_create_once(
        invocation,
        tool="backfill_commercial_facts",
        counts=counts,
        blockers=blockers,
        passed=not blockers,
        extra={
            "fabricated_purchase_count": 0,
            "fabricated_qa_pass_count": 0,
        },
    )
    if blockers:
        raise ValueError("commercial backfill reconciliation is blocking")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    args = parser.parse_args()
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
