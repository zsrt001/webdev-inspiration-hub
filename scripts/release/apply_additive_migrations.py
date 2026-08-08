#!/usr/bin/env python3
"""Apply only the reviewed forward additive chain through 20260710_0021."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from app.services.data_migration_checkpoint_service import (
    DataMigrationCheckpointService,
)
from scripts.release._migration_common import (
    _revalidate_batch_evidence,
    add_common_arguments,
    load_invocation,
    safe_main,
    write_report_create_once,
)
from scripts.release.repair_commercial_guard_row_shapes import (
    repair_commercial_guard_row_shapes,
)
from scripts.release.verify_commercial_database_roles import (
    prove_commercial_database_roles,
    verify_contract_matches_migration,
)


TARGET = "20260710_0021"
GUARD_REPAIR_PATH = ROOT / "scripts" / "release" / (
    "repair_commercial_guard_row_shapes.py"
)
MIGRATION_FILES = (
    "20260712_0014_repair_click_stats_values.py",
    "20260710_0014_web_identity_sessions.py",
    "20260710_0015_private_media_assets.py",
    "20260710_0016_commercial_ledger.py",
    "20260710_0017_creem_payment_facts.py",
    "20260710_0018_subscription_facts.py",
    "20260710_0019_generation_jobs.py",
    "20260710_0020_partner_consent.py",
    "20260710_0021_google_auth_only_activation.py",
)


def control_checksums() -> list[dict[str, str]]:
    raw = GUARD_REPAIR_PATH.read_bytes()
    return [
        {
            "file": GUARD_REPAIR_PATH.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    ]


def migration_checksums() -> list[dict[str, str]]:
    checksums: list[dict[str, str]] = []
    for name in MIGRATION_FILES:
        path = BACKEND / "alembic" / "versions" / name
        raw = path.read_bytes()
        checksums.append(
            {
                "file": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return checksums


async def _revision(db: AsyncSession) -> str:
    value = await db.scalar(text("SELECT version_num FROM alembic_version"))
    return str(value or "unknown")


async def _run(args: argparse.Namespace) -> None:
    if args.target_revision != TARGET:
        raise ValueError("only alembic upgrade 20260710_0021 is allowed")
    invocation = load_invocation(
        args,
        script_path=Path(__file__),
        mode="schema" if args.write else "dry",
        additional_script_paths=(GUARD_REPAIR_PATH,),
    )
    engine = create_async_engine(
        invocation.normalized_database_url,
        connect_args=invocation.connect_args,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    before = "unknown"
    after = "unknown"
    elapsed = 0.0
    checksums = migration_checksums()
    controls = control_checksums()
    _, database_role_contract_sha256 = verify_contract_matches_migration()
    database_role_proof: dict[str, object] = {
        "schema": "vowpic.commercial-7a-database-role-proof.v1",
        "passed": False,
        "status": "NOT_RUN",
        "reason": "dry_run",
        "contract_sha256": database_role_contract_sha256,
    }
    guard_repair_report: dict[str, object] = {
        "schema": "vowpic.schema-0020-cross-table-guard-repair.v2",
        "changed": False,
        "status": "NOT_RUN",
    }
    try:
        async with factory() as db:
            async with db.begin():
                _revalidate_batch_evidence(invocation)
                service = DataMigrationCheckpointService(db)
                await service.lock_contract(invocation.contract)
                before = await _revision(db)
                if before not in {
                    "20260710_0013",
                    "20260712_0014",
                    "20260710_0020",
                    TARGET,
                }:
                    raise ValueError(
                        f"unexpected Production revision before 7a migration: {before}"
                    )
                await service.checkpoint(
                    invocation.contract,
                    batch_boundary=f"before-{before}",
                    counts={"migration_file_count": len(checksums)},
                )

        if invocation.write and before != TARGET:
            environment = dict(os.environ)
            environment["DATABASE_URL"] = invocation.database_url
            existing_options = environment.get("PGOPTIONS", "").strip()
            bounded = "-c lock_timeout=5s -c statement_timeout=1800s"
            environment["PGOPTIONS"] = (
                f"{existing_options} {bounded}".strip()
            )
            started = time.monotonic()
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "alembic.ini",
                    "upgrade",
                    TARGET,
                ],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=2100,
                check=False,
            )
            elapsed = time.monotonic() - started
            if completed.returncode != 0:
                raise RuntimeError(
                    "forward additive migration failed; flags must remain OFF "
                    "and a reviewed forward fix is required"
                )

        async with factory() as db:
            async with db.begin():
                _revalidate_batch_evidence(invocation)
                service = DataMigrationCheckpointService(db)
                await service.lock_contract(invocation.contract)
                after = await _revision(db)
                if invocation.write and after != TARGET:
                    raise ValueError("Production revision did not reach 20260710_0021")
                if invocation.write:
                    guard_repair_report = (
                        await repair_commercial_guard_row_shapes(db)
                    )
                    database_role_proof = await prove_commercial_database_roles(db)
                counts = {
                    "migration_file_count": len(checksums),
                    "applied": int(invocation.write and before != TARGET),
                    "already_at_target": int(before == TARGET),
                    "guard_repair_changed": int(
                        bool(guard_repair_report.get("changed"))
                    ),
                }
                await service.checkpoint(
                    invocation.contract,
                    batch_boundary=f"after-{after}",
                    counts=counts,
                )
                await service.complete(invocation.contract, counts=counts)
        write_report_create_once(
            invocation,
            tool="apply_additive_migrations",
            counts={
                "migration_file_count": len(checksums),
                "applied": int(invocation.write and before != TARGET),
                "already_at_target": int(before == TARGET),
                "guard_repair_changed": int(
                    bool(guard_repair_report.get("changed"))
                ),
            },
            blockers={},
            passed=(after == TARGET if invocation.write else True),
            extra={
                "before_revision": before,
                "after_revision": after,
                "target_revision": TARGET,
                "duration_milliseconds": int(elapsed * 1000),
                "migration_checksums": checksums,
                "control_checksums": controls,
                "authorized_script_bundle_sha256": (
                    invocation.contract.script_sha256
                ),
                "lock_timeout_seconds": 5,
                "statement_timeout_seconds": 1800,
                "automatic_downgrade": False,
                "failure_disposition": "FORWARD_FIX_REQUIRED",
                "commercial_guard_repair": guard_repair_report,
                "database_role_proof": database_role_proof,
            },
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        SQLAlchemyError,
        subprocess.SubprocessError,
    ) as exc:
        if not invocation.report_path.exists():
            write_report_create_once(
                invocation,
                tool="apply_additive_migrations",
                counts={
                    "migration_file_count": len(checksums),
                    "applied": 0,
                    "already_at_target": int(before == TARGET),
                    "guard_repair_changed": 0,
                },
                blockers={"forward_fix_required": 1},
                passed=False,
                extra={
                    "before_revision": before,
                    "after_revision": after,
                    "target_revision": TARGET,
                    "duration_milliseconds": int(elapsed * 1000),
                    "migration_checksums": checksums,
                    "control_checksums": controls,
                    "authorized_script_bundle_sha256": (
                        invocation.contract.script_sha256
                    ),
                    "lock_timeout_seconds": 5,
                    "statement_timeout_seconds": 1800,
                    "automatic_downgrade": False,
                    "failure_disposition": "FORWARD_FIX_REQUIRED",
                    "failure_type": type(exc).__name__,
                    "commercial_guard_repair": guard_repair_report,
                    "database_role_proof": database_role_proof,
                },
            )
        if isinstance(exc, subprocess.TimeoutExpired):
            raise RuntimeError(
                "forward additive migration timed out; flags must remain OFF "
                "and a reviewed forward fix is required"
            ) from exc
        raise
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--target-revision", default=TARGET)
    args = parser.parse_args()
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
