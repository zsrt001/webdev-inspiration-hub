#!/usr/bin/env python3
"""Inventory or retire only reconciled pre-backend outbox envelopes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.services.legacy_outbox_retirement_service import (  # noqa: E402
    inventory_legacy_outbox,
    retire_legacy_outbox,
)


def _load_report(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise ValueError("legacy outbox inventory report is missing or too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("legacy outbox inventory report is invalid")
    return payload


def _write_create_once(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    )
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)


async def _run(args: argparse.Namespace, database_url: str) -> dict:
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(
        normalized_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as db:
            async with db.begin():
                if args.mode == "inventory":
                    return await inventory_legacy_outbox(
                        db,
                        source_sha=args.source_sha,
                    )
                inventory = _load_report(Path(args.inventory))
                if (
                    inventory.get("schema")
                    != "vowpic.legacy-outbox-retirement.v1"
                    or inventory.get("mode") != "inventory"
                    or inventory.get("source_sha") != args.source_sha
                ):
                    raise ValueError("legacy outbox inventory coordinates are invalid")
                return await retire_legacy_outbox(
                    db,
                    source_sha=args.source_sha,
                    expected_snapshot_sha256=str(
                        inventory.get("snapshot_sha256") or ""
                    ),
                )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventory", "apply"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--approval-id-env", default="DATA_MIGRATION_APPROVAL_ID")
    parser.add_argument("--inventory")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    args.source_sha = args.source_sha.strip().lower()

    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        print("NOT_RUN: protected database URL is required", file=sys.stderr)
        return 3
    if args.mode == "apply":
        if not args.inventory:
            print("NOT_RUN: inventory report is required for apply", file=sys.stderr)
            return 3
        if not os.environ.get(args.approval_id_env, "").strip():
            print("NOT_RUN: data-migration approval is required for apply", file=sys.stderr)
            return 3

    try:
        report = asyncio.run(_run(args, database_url))
        _write_create_once(Path(args.output), report)
    except Exception as exc:
        detail = str(exc).replace(database_url, "[REDACTED]")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "active_count": report["active_count"],
                "blocked_count": report["blocked_count"],
                "retired_count": len(report["retired_event_ids"]),
                "passed": report["passed"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
