#!/usr/bin/env python3
"""Normalize provable Supabase identities and disposition every legacy account."""

from __future__ import annotations

import argparse
import asyncio
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


_IDENTITY_NAMESPACE = UUID("783e365c-32b4-59eb-bc26-5517e0d3821f")
_SELECT = """
SELECT
  u.id,
  u.auth_provider,
  u.auth_subject,
  u.email,
  u.email_verified_at,
  u.status,
  (
    SELECT count(*) FROM user_identities ui
    WHERE ui.user_id = u.id AND ui.revoked_at IS NULL
  )::int AS active_identity_count,
  (
    SELECT count(*) FROM user_identities ui
    WHERE ui.provider = 'supabase'
      AND ui.subject = u.auth_subject
      AND ui.revoked_at IS NULL
  )::int AS subject_owner_count,
  EXISTS (
    SELECT 1 FROM user_account_merges m WHERE m.legacy_user_id = u.id
  ) AS already_merged,
  (
    u.avatar_url IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM media_assets ma
      WHERE ma.owner_user_id = u.id
        AND ma.status IN ('ACTIVE','PENDING_UPLOAD','PENDING_DELETE','DELETE_FAILED','QUARANTINED')
    )
    OR EXISTS (
      SELECT 1 FROM orders o
      WHERE o.user_id = u.id
        AND o.status IN ('CREATED','CHECKING','GENERATING','QUEUED','QA_PENDING',
                         'REPAIRING','UNKNOWN_EXTERNAL_STATE','CONSENT_REVIEW_REQUIRED')
    )
    OR EXISTS (
      SELECT 1 FROM generation_jobs gj JOIN orders o ON o.id = gj.order_id
      WHERE o.user_id = u.id AND gj.status IN ('QUEUED','ACTIVE','RECONCILING')
    )
    OR EXISTS (
      SELECT 1 FROM user_credits c WHERE c.user_id = u.id AND c.balance <> 0
    )
    OR EXISTS (
      SELECT 1 FROM payment_reconciliation_cases p
      WHERE p.user_id = u.id AND p.status <> 'RESOLVED'
    )
    OR EXISTS (
      SELECT 1 FROM account_claim_proofs cp
      WHERE cp.legacy_user_id = u.id AND cp.consumed_at IS NULL
        AND cp.expires_at > CURRENT_TIMESTAMP
    )
    OR EXISTS (
      SELECT 1 FROM user_subscriptions s
      WHERE s.user_id = u.id
        AND upper(s.status) IN ('CREATED','TRIALING','ACTIVE','PAST_DUE','CANCEL_PENDING')
    )
  ) AS has_blocking_facts
FROM users u
WHERE (:after IS NULL OR u.id::text > :after)
ORDER BY u.id::text
LIMIT :batch_size
"""


def classify_identity(row: dict[str, Any]) -> str:
    if bool(row["already_merged"]):
        return "MERGED"
    provider = str(row.get("auth_provider") or "").strip().lower()
    subject = str(row.get("auth_subject") or "").strip()
    identity_count = int(row.get("active_identity_count") or 0)
    subject_owners = int(row.get("subject_owner_count") or 0)
    if provider == "supabase" and subject:
        if identity_count <= 1 and subject_owners <= 1:
            return "NORMALIZED"
        return "QUARANTINED_BLOCKING"
    if bool(row["has_blocking_facts"]):
        return "QUARANTINED_BLOCKING"
    return "SOFT_CLOSED_TOMBSTONED"


async def _process_batch(db, rows, invocation):
    counts = {
        "NORMALIZED": 0,
        "MERGED": 0,
        "SOFT_CLOSED_TOMBSTONED": 0,
        "QUARANTINED_BLOCKING": 0,
    }
    blockers: dict[str, int] = {}
    for row in rows:
        disposition = classify_identity(row)
        counts[disposition] += 1
        user_id = UUID(str(row["id"]))
        if disposition == "QUARANTINED_BLOCKING":
            if not invocation.write:
                blockers["identity_conflict_or_active_lineage"] = (
                    blockers.get("identity_conflict_or_active_lineage", 0) + 1
                )
            else:
                await db.execute(
                    text(
                        """
                        UPDATE users
                        SET status = 'quarantined'
                        WHERE id = :user_id
                        """
                    ),
                    {"user_id": user_id},
                )
                await db.execute(
                    text(
                        """
                        UPDATE auth_sessions
                        SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": user_id},
                )
                await db.execute(
                    text(
                        """
                        UPDATE auth_refresh_tokens
                        SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                        WHERE session_id IN (
                          SELECT id FROM auth_sessions WHERE user_id = :user_id
                        )
                        """
                    ),
                    {"user_id": user_id},
                )
            continue
        if not invocation.write:
            continue
        if disposition == "NORMALIZED":
            identity_id = uuid5(_IDENTITY_NAMESPACE, f"supabase:{row['auth_subject']}")
            await db.execute(
                text(
                    """
                    INSERT INTO user_identities (
                      id, user_id, provider, subject, verified_email_snapshot
                    ) VALUES (
                      :id, :user_id, 'supabase', :subject, :verified_email
                    )
                    ON CONFLICT (provider, subject) DO NOTHING
                    """
                ),
                {
                    "id": identity_id,
                    "user_id": user_id,
                    "subject": str(row["auth_subject"]),
                    "verified_email": (
                        row["email"] if row["email_verified_at"] is not None else None
                    ),
                },
            )
            owner = await db.scalar(
                text(
                    """
                    SELECT user_id FROM user_identities
                    WHERE provider = 'supabase' AND subject = :subject
                      AND revoked_at IS NULL
                    """
                ),
                {"subject": str(row["auth_subject"])},
            )
            if str(owner) != str(user_id):
                raise ValueError("identity subject ownership conflict")
        elif disposition == "SOFT_CLOSED_TOMBSTONED":
            audit_request_id = (
                f"migration:{invocation.contract.child_run_id}:{str(user_id)[:8]}"
            )
            await db.execute(
                text(
                    """
                    INSERT INTO account_tombstones (
                      user_id, closure_reason, closed_at, media_cleanup_pending,
                      audit_request_id
                    ) VALUES (
                      :user_id, 'LEGACY_IDENTITY_RETIRED', CURRENT_TIMESTAMP,
                      false, :audit_request_id
                    )
                    ON CONFLICT (user_id) DO NOTHING
                    """
                ),
                {
                    "user_id": user_id,
                    "audit_request_id": audit_request_id,
                },
            )
            await db.execute(
                text(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": user_id},
            )
            await db.execute(
                text(
                    """
                    UPDATE auth_refresh_tokens
                    SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                    WHERE session_id IN (
                      SELECT id FROM auth_sessions WHERE user_id = :user_id
                    )
                    """
                ),
                {"user_id": user_id},
            )
            await db.execute(
                text(
                    """
                    UPDATE users
                    SET status = 'closed',
                        openid = NULL,
                        unionid = NULL,
                        username = NULL,
                        password = NULL,
                        auth_provider = NULL,
                        auth_subject = NULL,
                        email = NULL,
                        email_verified_at = NULL,
                        nickname = NULL,
                        avatar_url = NULL,
                        last_login_at = NULL
                    WHERE id = :user_id
                    """
                ),
                {"user_id": user_id},
            )
    return counts, blockers


async def _run(args: argparse.Namespace) -> None:
    invocation = load_invocation(args, script_path=Path(__file__))
    counts, blockers = await run_batch_migration(
        invocation,
        select_sql=_SELECT,
        process_batch=_process_batch,
    )
    if invocation.write and counts["QUARANTINED_BLOCKING"]:
        blockers["identity_conflict_or_active_lineage"] = counts[
            "QUARANTINED_BLOCKING"
        ]
    dispositions_complete = sum(counts.values()) == int(
        invocation.inventory["users"].get("total", 0)
    )
    if not dispositions_complete:
        blockers["identity_inventory_count_drift"] = 1
    passed = not blockers
    write_report_create_once(
        invocation,
        tool="backfill_identities",
        counts=counts,
        blockers=blockers,
        passed=passed,
        extra={
            "dispositions_complete": dispositions_complete,
            "fabricated_identity_count": 0,
        },
    )
    if not passed:
        raise ValueError("identity backfill has blocking dispositions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    args = parser.parse_args()
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
