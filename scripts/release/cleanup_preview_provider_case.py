#!/usr/bin/env python3
"""Cancel and remove only the deterministic Preview Provider acceptance case."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for entry in (str(ROOT), str(BACKEND)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.services.storage import DeleteResult, StorageService  # noqa: E402
from scripts.release.prepare_preview_provider_case import (  # noqa: E402
    _validate_activation,
    build_case_reference,
    case_id_for_activation,
    provider_case_object_key,
)
from scripts.release.prepare_preview_provider_grant import (  # noqa: E402
    is_provider_case_object_key,
    validate_input_reference,
)


def validate_case_row(reference: dict[str, Any], row: dict[str, Any]) -> None:
    normalized = validate_input_reference(reference)
    if not isinstance(row, dict):
        raise ValueError("Provider cleanup case row is invalid")
    for field in (
        "activation_id",
        "case_id",
        "source_sha",
        "runtime_bundle_id",
        "api_deployment_id",
        "backend_executor_digest",
        "job_id",
        "attempt_id",
        "asset_id",
    ):
        if str(row.get(field) or "") != str(normalized[field]):
            raise ValueError(f"Provider cleanup {field} coordinate mismatch")
    if not is_provider_case_object_key(normalized, row.get("object_key")):
        raise ValueError("Provider cleanup asset is outside the dedicated object prefix")
    if (
        str(row.get("owner_user_id") or "") != str(row.get("order_user_id") or "")
        or str(row.get("order_generation_job_id") or "") != normalized["job_id"]
        or row.get("order_source_asset_ids") != [normalized["asset_id"]]
        or str(row.get("job_active_attempt_id") or "") != normalized["attempt_id"]
    ):
        raise ValueError("Provider cleanup row graph ownership mismatch")
    if (
        int(row.get("order_price_cents") or 0) != 0
        or row.get("order_payment_id") is not None
        or row.get("order_paid_at") is not None
        or row.get("order_reservation_id") is not None
    ):
        raise ValueError("Provider cleanup refuses a case with financial facts")
    if int(row.get("acceptance_binding_count") or 0) < 1 or int(
        row.get("active_identity_count") or 0
    ) < 1:
        raise ValueError("Provider cleanup owner lacks acceptance identity authority")
    if row.get("job_lease_owner") is not None:
        raise ValueError("Provider cleanup refuses a leased generation job")
    if (
        str(row.get("job_api_deployment_id") or "") != normalized["api_deployment_id"]
        or str(row.get("job_runtime_bundle_id") or "") != normalized["runtime_bundle_id"]
        or str(row.get("job_worker_image_digest") or "")
        != normalized["backend_executor_digest"]
    ):
        raise ValueError("Provider cleanup job runtime coordinates mismatch")
    if str(row.get("attempt_provider") or "") != "evolink":
        raise ValueError("Provider cleanup refuses a foreign attempt")
    provider_task_id = str(row.get("attempt_provider_job_id") or "").strip()
    if provider_task_id:
        attempt_status = str(row.get("attempt_status") or "")
        job_status = str(row.get("job_status") or "")
        order_status = str(row.get("order_status") or "")
        terminal_graphs = {
            ("FINISHED", "FINISHED", "READY"),
            ("FINISHED", "FAILED", "FAILED"),
            ("FAILED", "FAILED", "FAILED"),
        }
        if (attempt_status, job_status, order_status) not in terminal_graphs:
            raise ValueError(
                "Provider cleanup refuses a submitted attempt before terminal settlement"
            )
    else:
        allowed_unsubmitted = {
            "order_status": {"QUEUED", "CANCELLED"},
            "job_status": {"QUEUED", "CANCELLED"},
            "attempt_status": {"PREPARED", "FAILED"},
        }
        for field, values in allowed_unsubmitted.items():
            if str(row.get(field) or "") not in values:
                raise ValueError(
                    f"Provider cleanup {field} is not safely terminalizable"
                )
    if str(row.get("asset_status") or "") not in {
        "PENDING_UPLOAD",
        "ACTIVE",
        "PENDING_DELETE",
        "DELETED",
    }:
        raise ValueError("Provider cleanup asset_status is not safely terminalizable")


def _database_url(value: str) -> str:
    clean = str(value or "").strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    if not clean:
        raise ValueError("Preview Provider cleanup database URL is required")
    return clean


def _row_query() -> str:
    return """
        SELECT ma.id::text AS asset_id, ma.owner_user_id::text AS owner_user_id,
               ma.object_key, ma.storage_provider AS asset_storage_provider,
               ma.status::text AS asset_status, ma.read_revoked_at AS asset_read_revoked_at,
               o.id::text AS order_id, o.user_id::text AS order_user_id,
               o.generation_job_id::text AS order_generation_job_id,
               o.source_asset_ids AS order_source_asset_ids,
               o.status::text AS order_status, o.price_cents AS order_price_cents,
               o.payment_id AS order_payment_id, o.paid_at AS order_paid_at,
               o.reservation_id::text AS order_reservation_id,
               gj.id::text AS job_id, gj.status::text AS job_status,
               gj.active_attempt_id::text AS job_active_attempt_id,
               gj.lease_owner AS job_lease_owner,
               gj.api_deployment_id AS job_api_deployment_id,
               gj.runtime_bundle_id AS job_runtime_bundle_id,
               gj.expected_worker_image_digest AS job_worker_image_digest,
               ga.id::text AS attempt_id, ga.status::text AS attempt_status,
               ga.provider AS attempt_provider, ga.provider_job_id AS attempt_provider_job_id,
               (SELECT count(*) FROM acceptance_identity_bindings aib
                 WHERE aib.consumed_user_id = ma.owner_user_id
                   AND aib.provider = 'google' AND aib.consumed_at IS NOT NULL
               ) AS acceptance_binding_count,
               (SELECT count(*) FROM user_identities ui
                 WHERE ui.user_id = ma.owner_user_id
                   AND ui.provider = 'supabase' AND ui.revoked_at IS NULL
               ) AS active_identity_count
          FROM media_assets ma
          JOIN generation_jobs gj ON gj.id = ma.job_id
          JOIN generation_attempts ga ON ga.id = gj.active_attempt_id
          JOIN orders o ON o.id = ma.order_id AND o.id = gj.order_id
         WHERE ma.object_key = %s
         FOR UPDATE OF ma, gj, ga, o
    """


def _build_reference(activation: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return build_case_reference(
        activation,
        owner_user_id=uuid.UUID(str(row["owner_user_id"])),
        order_id=uuid.UUID(str(row["order_id"])),
        job_id=uuid.UUID(str(row["job_id"])),
        attempt_id=uuid.UUID(str(row["attempt_id"])),
        asset_id=uuid.UUID(str(row["asset_id"])),
        backend_executor_digest=str(row["job_worker_image_digest"]),
    )


def cleanup_case(
    database_url: str,
    activation: dict[str, Any],
    *,
    now: datetime | None = None,
    store: StorageService | None = None,
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    normalized = _validate_activation(activation)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Provider cleanup timestamp must be timezone-aware")
    case_id = case_id_for_activation(normalized["activation_id"])
    object_key = provider_case_object_key(normalized["activation_id"], case_id)
    prefix = object_key.rsplit("/", 1)[0] + "/"
    url = _database_url(database_url)
    row: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    revoked_count = 0
    with psycopg2.connect(url) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(_row_query(), (object_key,))
            found = cursor.fetchone()
            if found is None:
                return {
                    "schema": "vowpic.preview-provider-case-cleanup.v1",
                    "state": "NOT_CREATED",
                    "activation_id": normalized["activation_id"],
                    "case_id": str(case_id),
                    "object_prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest(),
                    "observed_at": current.astimezone(timezone.utc).isoformat(),
                }
            row = dict(found)
            reference = _build_reference(normalized, row)
            row.update(reference)
            validate_case_row(reference, row)
            provider_task_bound = bool(
                str(row.get("attempt_provider_job_id") or "").strip()
            )
            cursor.execute(
                """
                UPDATE asset_access_grants
                   SET revoked_at = COALESCE(revoked_at, %s)
                 WHERE asset_id = %s AND job_id = %s AND attempt_id = %s
                   AND provider = 'evolink' AND purpose = 'generation-input'
                   AND runtime_bundle_id = %s AND target_api_deployment_id = %s
                """,
                (
                    current,
                    reference["asset_id"],
                    reference["job_id"],
                    reference["attempt_id"],
                    reference["runtime_bundle_id"],
                    reference["api_deployment_id"],
                ),
            )
            revoked_count = int(cursor.rowcount)
            if not provider_task_bound:
                cursor.execute(
                    """
                    UPDATE generation_attempts
                       SET status = 'FAILED', finished_at = COALESCE(finished_at, %s), updated_at = %s
                     WHERE id = %s AND status IN ('PREPARED','FAILED') AND provider_job_id IS NULL
                    """,
                    (current, current, reference["attempt_id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Provider cleanup attempt CAS failed")
                cursor.execute(
                    """
                    UPDATE generation_jobs
                       SET status = 'CANCELLED', finished_at = COALESCE(finished_at, %s),
                           last_error_code = 'preview_provider_case_cleanup', updated_at = %s
                     WHERE id = %s AND status IN ('QUEUED','CANCELLED') AND lease_owner IS NULL
                    """,
                    (current, current, reference["job_id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Provider cleanup job CAS failed")
                cursor.execute(
                    """
                    UPDATE orders
                       SET status = 'CANCELLED', error_message = 'preview_provider_case_cleanup', updated_at = %s
                     WHERE generation_job_id = %s AND status IN ('QUEUED','CANCELLED')
                       AND price_cents = 0 AND payment_id IS NULL AND paid_at IS NULL
                       AND reservation_id IS NULL
                    """,
                    (current, reference["job_id"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Provider cleanup order CAS failed")
            cursor.execute(
                """
                UPDATE media_assets
                   SET status = CASE WHEN status = 'DELETED' THEN status ELSE 'PENDING_DELETE' END,
                       read_revoked_at = COALESCE(read_revoked_at, %s),
                       deletion_reason = 'preview_provider_case_cleanup',
                       deletion_blockers = '[]'::jsonb, next_delete_at = %s, updated_at = %s
                 WHERE id = %s
                   AND status IN ('PENDING_UPLOAD','ACTIVE','PENDING_DELETE','DELETED')
                   AND lease_owner IS NULL
                """,
                (current, current, current, reference["asset_id"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Provider cleanup asset CAS failed")

    object_store = store or StorageService()
    before = object_store.list_private(prefix, limit=2)
    if any(key != object_key for key in before):
        raise ValueError("Provider cleanup dedicated prefix contains an unexpected object")
    deletion = object_store.delete_private(object_key)
    if deletion not in {DeleteResult.DELETED, DeleteResult.NOT_FOUND}:
        raise ValueError("Provider cleanup private object deletion failed")
    after = object_store.list_private(prefix, limit=1)
    if after:
        raise ValueError("Provider cleanup private object remains after deletion")

    with psycopg2.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE media_assets
                   SET status = 'DELETED', deleted_at = COALESCE(deleted_at, %s),
                       read_revoked_at = COALESCE(read_revoked_at, %s), updated_at = %s
                 WHERE id = %s AND status IN ('PENDING_DELETE','DELETED')
                """,
                (current, current, current, reference["asset_id"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Provider cleanup final asset CAS failed")
            cursor.execute(
                """
                SELECT count(*) FROM asset_access_grants
                 WHERE asset_id = %s AND revoked_at IS NULL
                """,
                (reference["asset_id"],),
            )
            if int(cursor.fetchone()[0]) != 0:
                raise ValueError("Provider cleanup left an active asset grant")
    return {
        "schema": "vowpic.preview-provider-case-cleanup.v1",
        "state": "CLEANED",
        "activation_id": normalized["activation_id"],
        "case_id": str(case_id),
        "job_id": reference["job_id"],
        "attempt_id": reference["attempt_id"],
        "asset_id": reference["asset_id"],
        "object_prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest(),
        "grants_revoked_or_already_revoked": revoked_count,
        "storage_delete_result": deletion.value,
        "cleanup_scope": "local-database-grant-and-private-object",
        "provider_task_bound": provider_task_bound,
        "terminal_generation_graph_preserved": provider_task_bound,
        "observed_at": current.astimezone(timezone.utc).isoformat(),
    }


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider cleanup activation input must be an object")
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-json", required=True)
    parser.add_argument("--database-url-env", default="PREVIEW_RUNTIME_DATABASE_URL")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = cleanup_case(
            os.environ.get(args.database_url_env, ""),
            _load_json(args.activation_json),
        )
        _write_create_once(Path(args.output), result)
        print(json.dumps({"state": result["state"], "case_id": result["case_id"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
