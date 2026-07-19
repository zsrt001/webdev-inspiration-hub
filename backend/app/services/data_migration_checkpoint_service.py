"""Lease/fence authority for audited Production migration batches.

Business migration code may mutate a batch only after this service has locked
and revalidated the durable parent and child contracts. Checkpoints are
append-only and contain sanitized counters only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_BUNDLE_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_SAFE_COORDINATE = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_RUN_NAMESPACE = UUID("f39a7fa0-25ee-52df-ae60-a976c10fa6ef")
_ACTIVE_PARENT_STATES = frozenset({"LEASED", "RUNNING", "ACTIVE"})
_ACTIVE_CHILD_STATES = frozenset({"RUNNING", "COMPLETED"})
_ALLOWED_MODES = frozenset(
    {
        "dry",
        "write",
        "copy-dry",
        "copy-write",
        "delete-dry",
        "delete-write",
        "replay",
        "schema",
    }
)


class MigrationControlError(RuntimeError):
    """A durable migration lease, fence, or checkpoint did not match."""


@dataclass(frozen=True)
class MigrationContract:
    parent_run_id: UUID
    script_run_id: str
    mode: str
    script_sha256: str
    inventory_sha256: str
    manifest_sha256: str
    runtime_bundle_id: str
    source_revision: str
    approval: str
    lease_owner: str

    @property
    def child_run_id(self) -> UUID:
        return uuid5(
            _RUN_NAMESPACE,
            f"{self.parent_run_id}:{self.script_run_id}",
        )


@dataclass(frozen=True)
class LockedMigrationRun:
    parent_run_id: UUID
    child_run_id: UUID
    release_activation_id: UUID
    fencing_token: int
    lease_expires_at: datetime


def validate_contract(contract: MigrationContract) -> None:
    if contract.mode not in _ALLOWED_MODES:
        raise MigrationControlError("migration mode is not allowed")
    for value, label in (
        (contract.script_sha256, "script SHA-256"),
        (contract.inventory_sha256, "inventory SHA-256"),
        (contract.manifest_sha256, "manifest SHA-256"),
    ):
        if not _SHA256.fullmatch(str(value or "")):
            raise MigrationControlError(f"{label} is invalid")
    if not _RUNTIME_BUNDLE_ID.fullmatch(contract.runtime_bundle_id):
        raise MigrationControlError("runtime bundle ID is invalid")
    for value, label in (
        (contract.script_run_id, "script run ID"),
        (contract.source_revision, "source revision"),
        (contract.approval, "approval"),
        (contract.lease_owner, "lease owner"),
    ):
        if not _SAFE_COORDINATE.fullmatch(str(value or "")):
            raise MigrationControlError(f"{label} is invalid")


def sanitize_counts(counts: Mapping[str, Any]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for key, value in sorted(counts.items()):
        clean_key = str(key or "").strip()
        if not _SAFE_COORDINATE.fullmatch(clean_key):
            raise MigrationControlError("checkpoint count key is invalid")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MigrationControlError("checkpoint counts must be non-negative integers")
        sanitized[clean_key] = value
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 8192:
        raise MigrationControlError("checkpoint counts exceed the audit limit")
    return sanitized


def script_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _aware_utc(value: object, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise MigrationControlError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise MigrationControlError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_equal(row: Mapping[str, Any], expected: Mapping[str, object]) -> None:
    mismatches = [
        key
        for key, value in expected.items()
        if str(row.get(key)) != str(value)
    ]
    if mismatches:
        raise MigrationControlError(
            "migration run contract drift: " + ", ".join(sorted(mismatches))
        )


class DataMigrationCheckpointService:
    """Revalidate the parent/child lease immediately before every batch write."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lock_contract(
        self,
        contract: MigrationContract,
        *,
        now: datetime | None = None,
    ) -> LockedMigrationRun:
        validate_contract(contract)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('vowpic:data-migration:production', 0))"
            )
        )
        result = await self.db.execute(
            text(
                """
                SELECT *
                FROM data_migration_runs
                WHERE id = :parent_run_id
                FOR UPDATE
                """
            ),
            {"parent_run_id": contract.parent_run_id},
        )
        parent = result.mappings().one_or_none()
        if parent is None:
            raise MigrationControlError("migration parent run is missing")
        if parent["parent_run_id"] is not None:
            raise MigrationControlError("migration parent points to another parent")
        if parent["environment"] != "production":
            raise MigrationControlError("migration parent is not Production")
        if str(parent["state"]) not in _ACTIVE_PARENT_STATES:
            raise MigrationControlError("migration parent lease is not active")
        lease_expires_at = _aware_utc(
            parent["lease_expires_at"],
            label="parent lease expiry",
        )
        if lease_expires_at <= current:
            raise MigrationControlError("migration parent lease has expired")
        expected_parent: dict[str, object] = {
            "runtime_bundle_id": contract.runtime_bundle_id,
            "manifest_sha256": contract.manifest_sha256,
        }
        if contract.mode in {
            "write",
            "copy-write",
            "delete-write",
            "replay",
            "schema",
        }:
            expected_parent["approval"] = contract.approval
        _require_equal(parent, expected_parent)
        fencing_token = int(parent["fencing_token"])
        if fencing_token <= 0:
            raise MigrationControlError("migration parent fencing token is invalid")

        child_result = await self.db.execute(
            text(
                """
                SELECT *
                FROM data_migration_runs
                WHERE id = :child_run_id
                FOR UPDATE
                """
            ),
            {"child_run_id": contract.child_run_id},
        )
        child = child_result.mappings().one_or_none()
        expected_child = {
            "parent_run_id": contract.parent_run_id,
            "release_activation_id": parent["release_activation_id"],
            "environment": "production",
            "runtime_bundle_id": contract.runtime_bundle_id,
            "manifest_sha256": contract.manifest_sha256,
            "inventory_sha256": contract.inventory_sha256,
            "script_sha256": contract.script_sha256,
            "source_revision": contract.source_revision,
            "mode": contract.mode,
            "approval": contract.approval,
            "lease_owner": contract.lease_owner,
            "fencing_token": fencing_token,
        }
        if child is None:
            await self.db.execute(
                text(
                    """
                    INSERT INTO data_migration_runs (
                        id, parent_run_id, release_activation_id, environment,
                        runtime_bundle_id, manifest_sha256, inventory_sha256,
                        script_sha256, source_revision, target_revision, mode,
                        approval, lease_owner, lease_expires_at, heartbeat_at,
                        fencing_token, state, counts_json
                    ) VALUES (
                        :id, :parent_run_id, :release_activation_id, 'production',
                        :runtime_bundle_id, :manifest_sha256, :inventory_sha256,
                        :script_sha256, :source_revision, NULL, :mode,
                        :approval, :lease_owner, :lease_expires_at, :heartbeat_at,
                        :fencing_token, 'RUNNING', '{}'::jsonb
                    )
                    """
                ),
                {
                    "id": contract.child_run_id,
                    **expected_child,
                    "lease_expires_at": lease_expires_at,
                    "heartbeat_at": current,
                },
            )
        else:
            if str(child["state"]) not in _ACTIVE_CHILD_STATES:
                raise MigrationControlError("migration child is not resumable")
            _require_equal(child, expected_child)
            if _aware_utc(child["lease_expires_at"], label="child lease expiry") <= current:
                raise MigrationControlError("migration child lease has expired")
            await self.db.execute(
                text(
                    """
                    UPDATE data_migration_runs
                    SET heartbeat_at = :heartbeat_at
                    WHERE id = :child_run_id
                      AND fencing_token = :fencing_token
                    """
                ),
                {
                    "heartbeat_at": current,
                    "child_run_id": contract.child_run_id,
                    "fencing_token": fencing_token,
                },
            )
        return LockedMigrationRun(
            parent_run_id=contract.parent_run_id,
            child_run_id=contract.child_run_id,
            release_activation_id=UUID(str(parent["release_activation_id"])),
            fencing_token=fencing_token,
            lease_expires_at=lease_expires_at,
        )

    async def checkpoint(
        self,
        contract: MigrationContract,
        *,
        batch_boundary: str,
        counts: Mapping[str, Any],
        now: datetime | None = None,
    ) -> LockedMigrationRun:
        locked = await self.lock_contract(contract, now=now)
        boundary = str(batch_boundary or "").strip()
        if not _SAFE_COORDINATE.fullmatch(boundary):
            raise MigrationControlError("batch boundary is invalid")
        safe_counts = sanitize_counts(counts)
        existing_result = await self.db.execute(
            text(
                """
                SELECT counts_json
                FROM data_migration_checkpoints
                WHERE run_id = :run_id
                  AND script_sha256 = :script_sha256
                  AND mode = :mode
                  AND batch_boundary = :batch_boundary
                """
            ),
            {
                "run_id": locked.child_run_id,
                "script_sha256": contract.script_sha256,
                "mode": contract.mode,
                "batch_boundary": boundary,
            },
        )
        existing = existing_result.mappings().one_or_none()
        if existing is not None:
            if dict(existing["counts_json"] or {}) != safe_counts:
                raise MigrationControlError("checkpoint replay counts conflict")
            return locked
        await self.db.execute(
            text(
                """
                INSERT INTO data_migration_checkpoints (
                    id, run_id, script_sha256, mode, batch_boundary,
                    inventory_sha256, manifest_sha256, approval, counts_json
                ) VALUES (
                    gen_random_uuid(), :run_id, :script_sha256, :mode,
                    :batch_boundary, :inventory_sha256, :manifest_sha256,
                    :approval, CAST(:counts_json AS jsonb)
                )
                """
            ),
            {
                "run_id": locked.child_run_id,
                "script_sha256": contract.script_sha256,
                "mode": contract.mode,
                "batch_boundary": boundary,
                "inventory_sha256": contract.inventory_sha256,
                "manifest_sha256": contract.manifest_sha256,
                "approval": contract.approval,
                "counts_json": json.dumps(
                    safe_counts,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
        return locked

    async def last_checkpoint(
        self,
        contract: MigrationContract,
        *,
        now: datetime | None = None,
    ) -> tuple[str | None, dict[str, int]]:
        locked = await self.lock_contract(contract, now=now)
        result = await self.db.execute(
            text(
                """
                SELECT batch_boundary, counts_json
                FROM data_migration_checkpoints
                WHERE run_id = :run_id
                  AND script_sha256 = :script_sha256
                  AND mode = :mode
                ORDER BY created_at DESC, batch_boundary DESC
                LIMIT 1
                """
            ),
            {
                "run_id": locked.child_run_id,
                "script_sha256": contract.script_sha256,
                "mode": contract.mode,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None, {}
        return str(row["batch_boundary"]), sanitize_counts(
            dict(row["counts_json"] or {})
        )

    async def complete(
        self,
        contract: MigrationContract,
        *,
        counts: Mapping[str, Any],
        now: datetime | None = None,
    ) -> LockedMigrationRun:
        locked = await self.lock_contract(contract, now=now)
        safe_counts = sanitize_counts(counts)
        result = await self.db.execute(
            text(
                """
                UPDATE data_migration_runs
                SET state = 'COMPLETED',
                    counts_json = CAST(:counts_json AS jsonb),
                    heartbeat_at = :heartbeat_at
                WHERE id = :child_run_id
                  AND parent_run_id = :parent_run_id
                  AND fencing_token = :fencing_token
                  AND state IN ('RUNNING', 'COMPLETED')
                RETURNING id
                """
            ),
            {
                "counts_json": json.dumps(
                    safe_counts,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "heartbeat_at": (now or datetime.now(timezone.utc)).astimezone(
                    timezone.utc
                ),
                "child_run_id": locked.child_run_id,
                "parent_run_id": locked.parent_run_id,
                "fencing_token": locked.fencing_token,
            },
        )
        if result.scalar_one_or_none() is None:
            raise MigrationControlError("migration child completion lost its fence")
        return locked
