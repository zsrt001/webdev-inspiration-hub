"""Shared evidence and CLI contract for Task-28 migration tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from app.core.database import normalize_database_url  # noqa: E402
from app.services.data_migration_checkpoint_service import (  # noqa: E402
    DataMigrationCheckpointService,
    MigrationContract,
    script_sha256,
)
from app.services.production_inventory_service import hmac_identifier  # noqa: E402
from scripts.release.build_manifest import (  # noqa: E402
    canonical_manifest_bytes,
    validate_manifest,
)
from scripts.release.inventory_production import (  # noqa: E402
    source_database_identity,
)
from scripts.release.verify_inventory_signature import (  # noqa: E402
    verify_inventory_evidence,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_SENSITIVE_REPORT_KEYS = {
    "email",
    "openid",
    "url",
    "token",
    "password",
    "auth_subject",
    "provider_subject",
    "object_key",
    "database_url",
}


@dataclass(frozen=True)
class MigrationInvocation:
    database_url: str
    normalized_database_url: str
    connect_args: dict[str, Any]
    hmac_key: bytes
    inventory: dict[str, Any]
    inventory_sha256: str
    inventory_report_path: Path
    inventory_signature_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    release_manifest_path: Path
    script_path: Path
    approval: str
    contract: MigrationContract
    report_path: Path
    batch_size: int
    resume: bool
    write: bool


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    modes: tuple[str, ...] = ("dry", "write"),
) -> None:
    parser.add_argument(
        "--database-url-env",
        default="PRODUCTION_MIGRATION_DATABASE_URL",
    )
    parser.add_argument("--migration-parent-run-id", required=True)
    parser.add_argument("--script-run-id", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--inventory-report", required=True)
    parser.add_argument("--inventory-signature", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--inventory-hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--approval-id-env", default="DATA_MIGRATION_APPROVAL_ID")
    parser.add_argument("--report", required=True)
    if set(modes) == {"dry", "write"}:
        choice = parser.add_mutually_exclusive_group(required=True)
        choice.add_argument("--dry-run", action="store_true")
        choice.add_argument("--write", action="store_true")
    else:
        parser.add_argument("--mode", required=True, choices=modes)
        execution = parser.add_mutually_exclusive_group(required=True)
        execution.add_argument("--dry-run", action="store_true")
        execution.add_argument("--write", action="store_true")


def _load_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    expected = str(expected_sha256 or "").strip().lower()
    if not _SHA256.fullmatch(expected):
        raise ValueError("expected manifest SHA-256 is invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("release manifest SHA-256 mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is invalid JSON") from exc
    normalized = validate_manifest(payload)
    if canonical_manifest_bytes(normalized) != raw:
        raise ValueError("release manifest is not canonical")
    if normalized["release_role"] != "COMMERCIAL_7A":
        raise ValueError("migration requires a COMMERCIAL_7A manifest")
    return normalized


def load_invocation(
    args: argparse.Namespace,
    *,
    script_path: Path,
    mode: str | None = None,
) -> MigrationInvocation:
    database_url = os.environ.get(args.database_url_env, "").strip()
    hmac_key = os.environ.get(args.inventory_hmac_key_env, "").encode("utf-8")
    if not database_url:
        raise ValueError("protected migration database URL is required")
    if len(hmac_key) < 32:
        raise ValueError("inventory HMAC key must contain at least 32 bytes")
    inventory_report_path = Path(args.inventory_report)
    inventory_signature_path = Path(args.inventory_signature)
    release_manifest_path = Path(args.release_manifest)
    normalized_inventory_sha256 = str(
        args.expected_inventory_sha256 or ""
    ).strip().lower()
    normalized_manifest_sha256 = str(
        args.expected_manifest_sha256 or ""
    ).strip().lower()
    resolved_script_path = script_path.resolve()
    inventory = verify_inventory_evidence(
        report_path=inventory_report_path,
        signature_path=inventory_signature_path,
        expected_sha256=normalized_inventory_sha256,
        hmac_key=hmac_key,
    )
    expected_database_identity = hmac_identifier(
        hmac_key,
        "source_database",
        source_database_identity(database_url),
    )
    if (
        inventory["source_database_identity_hmac_sha256"]
        != expected_database_identity
    ):
        raise ValueError("inventory source database identity drift")
    manifest = _load_manifest(
        release_manifest_path,
        normalized_manifest_sha256,
    )
    if manifest["schema_revision"] != "20260710_0020":
        raise ValueError("COMMERCIAL_7A manifest must target schema 20260710_0020")
    if not 1 <= int(args.batch_size) <= 5000:
        raise ValueError("batch size must be between 1 and 5000")
    try:
        parent_run_id = UUID(str(args.migration_parent_run_id))
    except ValueError as exc:
        raise ValueError("migration parent run ID is invalid") from exc
    script_run_id = str(args.script_run_id or "").strip()
    if not _SAFE_ID.fullmatch(script_run_id):
        raise ValueError("script run ID is invalid")
    write = bool(args.write)
    effective_mode = mode or ("write" if write else "dry")
    approval = os.environ.get(args.approval_id_env, "").strip()
    if write and not approval:
        raise ValueError("write mode requires DATA_MIGRATION_APPROVAL_ID")
    if not approval:
        approval = "DRY_RUN_NO_PRODUCTION_WRITE"
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    if write and (
        not run_id.isdigit()
        or not run_attempt.isdigit()
        or int(run_id) < 1
        or int(run_attempt) < 1
    ):
        raise ValueError("write mode requires authenticated workflow run coordinates")
    lease_owner = (
        f"github-{run_id}-{run_attempt}-{script_run_id}"
        if run_id and run_attempt
        else f"local-dry-{script_run_id}"
    )
    normalized_url, connect_args = normalize_database_url(database_url)
    contract = MigrationContract(
        parent_run_id=parent_run_id,
        script_run_id=script_run_id,
        mode=effective_mode,
        script_sha256=script_sha256(str(resolved_script_path)),
        inventory_sha256=normalized_inventory_sha256,
        manifest_sha256=normalized_manifest_sha256,
        runtime_bundle_id=manifest["runtime_bundle_id"],
        source_revision=str(inventory["schema_revision"]),
        approval=approval,
        lease_owner=lease_owner[:256],
    )
    return MigrationInvocation(
        database_url=database_url,
        normalized_database_url=normalized_url,
        connect_args=connect_args,
        hmac_key=hmac_key,
        inventory=inventory,
        inventory_sha256=normalized_inventory_sha256,
        inventory_report_path=inventory_report_path,
        inventory_signature_path=inventory_signature_path,
        manifest=manifest,
        manifest_sha256=normalized_manifest_sha256,
        release_manifest_path=release_manifest_path,
        script_path=resolved_script_path,
        approval=approval,
        contract=contract,
        report_path=Path(args.report),
        batch_size=int(args.batch_size),
        resume=bool(args.resume),
        write=write,
    )


def _revalidate_batch_evidence(invocation: MigrationInvocation) -> None:
    inventory = verify_inventory_evidence(
        report_path=invocation.inventory_report_path,
        signature_path=invocation.inventory_signature_path,
        expected_sha256=invocation.inventory_sha256,
        hmac_key=invocation.hmac_key,
    )
    expected_database_identity = hmac_identifier(
        invocation.hmac_key,
        "source_database",
        source_database_identity(invocation.database_url),
    )
    if (
        inventory["source_database_identity_hmac_sha256"]
        != expected_database_identity
    ):
        raise ValueError("inventory source database identity drift")
    manifest = _load_manifest(
        invocation.release_manifest_path,
        invocation.manifest_sha256,
    )
    if manifest != invocation.manifest:
        raise ValueError("release manifest changed after invocation binding")
    if (
        script_sha256(str(invocation.script_path))
        != invocation.contract.script_sha256
    ):
        raise ValueError("migration script changed after invocation binding")


def _scan_sensitive_keys(value: object, *, path: str = "report") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SENSITIVE_REPORT_KEYS or normalized.endswith(
                ("_url", "_token", "_email", "_subject", "_object_key")
            ):
                raise ValueError(f"sensitive report field is forbidden: {path}.{key}")
            _scan_sensitive_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_sensitive_keys(nested, path=f"{path}[{index}]")


def write_report_create_once(
    invocation: MigrationInvocation,
    *,
    tool: str,
    counts: dict[str, int],
    passed: bool,
    blockers: dict[str, int] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "vowpic.data-migration-report.v1",
        "tool": tool,
        "mode": invocation.contract.mode,
        "passed": bool(passed),
        "write_performed": invocation.write,
        "parent_run_id": str(invocation.contract.parent_run_id),
        "child_run_id": str(invocation.contract.child_run_id),
        "script_sha256": invocation.contract.script_sha256,
        "inventory_sha256": invocation.inventory_sha256,
        "manifest_sha256": invocation.manifest_sha256,
        "runtime_bundle_id": invocation.contract.runtime_bundle_id,
        "source_revision": invocation.contract.source_revision,
        "counts": dict(sorted(counts.items())),
        "blockers": dict(sorted((blockers or {}).items())),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload["details"] = extra
    _scan_sensitive_keys(payload)
    raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    invocation.report_path.parent.mkdir(parents=True, exist_ok=True)
    with invocation.report_path.open("xb") as handle:
        handle.write(raw)
    return payload


async def run_batch_migration(
    invocation: MigrationInvocation,
    *,
    select_sql: str,
    process_batch: Callable[
        [Any, list[dict[str, Any]], MigrationInvocation],
        Awaitable[tuple[dict[str, int], dict[str, int]]],
    ],
) -> tuple[dict[str, int], dict[str, int]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    class _BlockedWriteBatch(RuntimeError):
        def __init__(
            self,
            *,
            blockers: dict[str, int],
            row_count: int,
        ) -> None:
            super().__init__("write batch contains blocking rows")
            self.blockers = blockers
            self.row_count = row_count

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
    counts: dict[str, int] = {}
    blockers: dict[str, int] = {}
    boundary: str | None = None

    async def require_source_revision(db: Any) -> None:
        actual = str(
            await db.scalar(text("SELECT version_num FROM alembic_version"))
            or "unknown"
        )
        if actual != invocation.contract.source_revision:
            raise ValueError(
                "migration source revision drift: "
                f"expected {invocation.contract.source_revision}, got {actual}"
            )

    try:
        async with factory() as db:
            async with db.begin():
                _revalidate_batch_evidence(invocation)
                await require_source_revision(db)
                service = DataMigrationCheckpointService(db)
                existing_boundary, existing_counts = await service.last_checkpoint(
                    invocation.contract
                )
                await require_source_revision(db)
                if existing_boundary and not invocation.resume:
                    raise ValueError(
                        "existing checkpoint requires explicit --resume"
                    )
                if invocation.resume:
                    boundary = existing_boundary
                    counts.update(existing_counts)

        while True:
            try:
                async with factory() as db:
                    async with db.begin():
                        _revalidate_batch_evidence(invocation)
                        service = DataMigrationCheckpointService(db)
                        await service.lock_contract(invocation.contract)
                        await require_source_revision(db)
                        result = await db.execute(
                            text(select_sql),
                            {
                                "after": boundary,
                                "batch_size": invocation.batch_size,
                            },
                        )
                        rows = [dict(row) for row in result.mappings().all()]
                        if not rows:
                            await service.complete(
                                invocation.contract,
                                counts=counts,
                            )
                            break
                        delta, batch_blockers = await process_batch(
                            db,
                            rows,
                            invocation,
                        )
                        if invocation.write and batch_blockers:
                            # Raising inside the transaction is deliberate: any
                            # writes already attempted by the processor are
                            # rolled back, and neither the boundary nor the
                            # durable checkpoint advances past blocking rows.
                            raise _BlockedWriteBatch(
                                blockers=batch_blockers,
                                row_count=len(rows),
                            )
                        for key, value in delta.items():
                            counts[key] = counts.get(key, 0) + int(value)
                        for key, value in batch_blockers.items():
                            blockers[key] = blockers.get(key, 0) + int(value)
                        boundary = str(rows[-1]["id"])
                        await service.checkpoint(
                            invocation.contract,
                            batch_boundary=boundary,
                            counts=counts,
                        )
            except _BlockedWriteBatch as blocked:
                for key, value in blocked.blockers.items():
                    blockers[key] = blockers.get(key, 0) + int(value)
                counts["blocked_batches_rolled_back"] = (
                    counts.get("blocked_batches_rolled_back", 0) + 1
                )
                counts["blocked_rows_rolled_back"] = (
                    counts.get("blocked_rows_rolled_back", 0)
                    + blocked.row_count
                )
                return counts, blockers
        return counts, blockers
    finally:
        await engine.dispose()


def safe_main(run) -> int:
    try:
        run()
        return 0
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
