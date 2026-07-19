#!/usr/bin/env python3
"""Replay already verified Provider events after schema 0020 and prove zero residue."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.payment_event import PaymentEvent, PaymentEventProcessingState
from app.services.payment_service import payment_service


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _async_database_url(value: str) -> str:
    url = str(value or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("event replay database URL is invalid")
    return url


def build_replay_report(
    *,
    manifest_sha256: str,
    before: dict[str, int],
    replayed_count: int,
    after: dict[str, int],
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    manifest = str(manifest_sha256 or "").strip().lower()
    if not SHA256.fullmatch(manifest):
        raise ValueError("event replay manifest SHA-256 is invalid")
    for label, counts in (("before", before), ("after", after)):
        if set(counts) != {
            "RECEIVED", "UNHANDLED", "APPLIED", "RECONCILIATION_REQUIRED"
        } or any(not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ValueError(f"event replay {label} counts are invalid")
    if not isinstance(replayed_count, int) or replayed_count < 0:
        raise ValueError("event replay count is invalid")
    blockers = {
        state: after[state]
        for state in ("RECEIVED", "UNHANDLED", "RECONCILIATION_REQUIRED")
        if after[state]
    }
    if blockers:
        raise ValueError(f"event replay left blocking states: {blockers}")
    return {
        "schema": "vowpic.migration-window-event-replay.v1",
        "passed": True,
        "manifest_sha256": manifest,
        "before_counts": before,
        "replayed_count": replayed_count,
        "after_counts": after,
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
    }


async def _state_counts(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(PaymentEvent.processing_state)
    )
    counts = {state.value: 0 for state in PaymentEventProcessingState}
    for value in result.scalars().all():
        state = value.value if isinstance(value, PaymentEventProcessingState) else str(value)
        if state not in counts:
            raise ValueError("payment event has an unknown processing state")
        counts[state] += 1
    return counts


async def replay_events(database_url: str) -> tuple[dict[str, int], int, dict[str, int]]:
    engine = create_async_engine(
        _async_database_url(database_url), pool_pre_ping=True, pool_recycle=120
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    replayed = 0
    try:
        async with factory() as session:
            before = await _state_counts(session)
        while True:
            async with factory() as session:
                async with session.begin():
                    event_id = await session.scalar(
                        select(PaymentEvent.id)
                        .where(
                            PaymentEvent.processing_state
                            == PaymentEventProcessingState.RECEIVED
                        )
                        .order_by(
                            PaymentEvent.occurred_at.asc().nullsfirst(),
                            PaymentEvent.created_at.asc(),
                            PaymentEvent.id.asc(),
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    if event_id is None:
                        break
                    await payment_service.apply_payment_event(
                        session, payment_event_id=event_id
                    )
                    replayed += 1
        async with factory() as session:
            after = await _state_counts(session)
        return before, replayed, after
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        database_url = os.environ.get(args.database_url_env, "")
        before, replayed, after = asyncio.run(replay_events(database_url))
        report = build_replay_report(
            manifest_sha256=args.manifest_sha256,
            before=before,
            replayed_count=replayed,
            after=after,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
