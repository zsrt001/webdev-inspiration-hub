#!/usr/bin/env python3
"""Copy approved public user media to Private Blob, switch facts, then delete."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import unquote, urlsplit
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
from scripts.release.backfill_media_assets import (
    classify_media_reference,
)


_ASSET_NAMESPACE = UUID("fbc762b3-c55d-57e9-bf9f-cb2b4678fe55")
_SELECT = """
WITH refs AS (
  SELECT 'order_source'::text AS source_kind, o.id AS source_row_id,
         o.user_id AS owner_user_id, value #>> '{}' AS reference,
         o.generation_job_id AS job_id,
         COALESCE(o.source_images_expires_at, CURRENT_TIMESTAMP + interval '180 days') AS expires_at
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.source_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'order_preview', o.id, o.user_id, value #>> '{}',
         o.generation_job_id,
         COALESCE(o.expires_at, CURRENT_TIMESTAMP + interval '180 days')
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.preview_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'order_final', o.id, o.user_id, value #>> '{}',
         o.generation_job_id,
         COALESCE(o.expires_at, CURRENT_TIMESTAMP + interval '180 days')
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.final_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'live_portrait_source', lp.id, lp.user_id, lp.source_image_url,
         NULL::uuid,
         CURRENT_TIMESTAMP + interval '180 days'
  FROM live_portrait_jobs lp WHERE NULLIF(lp.source_image_url, '') IS NOT NULL
  UNION ALL
  SELECT 'live_portrait_video', lp.id, lp.user_id, lp.video_url,
         NULL::uuid,
         CURRENT_TIMESTAMP + interval '180 days'
  FROM live_portrait_jobs lp WHERE NULLIF(lp.video_url, '') IS NOT NULL
), counts AS (
  SELECT reference, count(*)::int AS global_reference_count,
         count(DISTINCT owner_user_id)::int AS owner_count
  FROM refs GROUP BY reference
), normalized AS (
  SELECT
    refs.source_kind || ':' || refs.source_row_id::text || ':' ||
      md5(refs.reference) AS id,
    refs.*,
    counts.global_reference_count,
    counts.owner_count,
    EXISTS (SELECT 1 FROM users u WHERE u.id = refs.owner_user_id) AS owner_exists
  FROM refs JOIN counts USING (reference)
)
SELECT *
FROM normalized
WHERE (:after IS NULL OR id > :after)
ORDER BY id
LIMIT :batch_size
"""


def _exact_origin(value: str, *, label: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"{label} must be an exact HTTPS origin")
    return f"https://{parsed.netloc.lower()}"


def _reference_path(reference: str, *, approved_origin: str) -> str:
    parsed = urlsplit(str(reference or "").strip())
    origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
    if origin != approved_origin or parsed.query or parsed.fragment:
        raise ValueError("legacy reference is not from the approved public origin")
    if "%" in parsed.path:
        raise ValueError("legacy public object path must not be percent encoded")
    path = unquote(parsed.path).lstrip("/")
    if (
        not path
        or len(path) > 1024
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", path)
    ):
        raise ValueError("legacy public object path is invalid")
    return path


def _mime_type(content: bytes, source_kind: str) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return "video/mp4"
    if source_kind == "live_portrait_video":
        raise ValueError("legacy video MIME type is unsupported")
    raise ValueError("legacy image MIME type is unsupported")


def _asset_role(source_kind: str) -> str:
    return {
        "order_source": "source",
        "order_preview": "preview_watermarked",
        "order_final": "final_master",
        "live_portrait_source": "source",
        "live_portrait_video": "legacy_video",
    }[source_kind]


def legacy_media_asset_id(
    *,
    source_kind: str,
    source_row_id: object,
    reference: str,
) -> UUID:
    reference_hash = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    return uuid5(
        _ASSET_NAMESPACE,
        f"{source_kind}:{source_row_id}:{reference_hash}",
    )


class _VercelBlob:
    def __init__(
        self,
        *,
        public_origin: str,
        private_origin: str,
        public_read_token: str | None = None,
        public_delete_token: str | None = None,
        private_read_token: str | None = None,
        private_write_token: str | None = None,
    ) -> None:
        self.public_origin = public_origin
        self.private_origin = private_origin
        self.public_read_token = public_read_token
        self.public_delete_token = public_delete_token
        self.private_read_token = private_read_token
        self.private_write_token = private_write_token

    @staticmethod
    def _content(result: object) -> bytes:
        if isinstance(result, dict):
            return bytes(result.get("content") or b"")
        return bytes(getattr(result, "content", b"") or b"")

    @staticmethod
    def _result_origin(result: object) -> str:
        raw = (
            result.get("url")
            if isinstance(result, dict)
            else getattr(result, "url", "")
        )
        parsed = urlsplit(str(raw or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Blob SDK returned an invalid object URL")
        return f"https://{parsed.netloc.lower()}"

    def read_public(self, path: str) -> bytes:
        if not self.public_read_token:
            raise ValueError("legacy public read token is missing")
        from vercel.blob import get

        result = get(
            path,
            access="public",
            token=self.public_read_token,
            timeout=30.0,
            use_cache=False,
        )
        if self._result_origin(result) != self.public_origin:
            raise ValueError("public Blob read resolved to the wrong store")
        content = self._content(result)
        if not content:
            raise ValueError("legacy public object is empty or unavailable")
        return content

    def read_private(self, path: str) -> bytes:
        token = self.private_read_token or self.private_write_token
        if not token:
            raise ValueError("private Blob read token is missing")
        from vercel.blob import get

        result = get(
            path,
            access="private",
            token=token,
            timeout=30.0,
            use_cache=False,
        )
        if self._result_origin(result) != self.private_origin:
            raise ValueError("private Blob read resolved to the wrong store")
        return self._content(result)

    def put_private(self, path: str, content: bytes, mime_type: str) -> None:
        if not self.private_write_token:
            raise ValueError("private Blob write token is missing")
        from vercel.blob import put

        result = put(
            path,
            content,
            access="private",
            content_type=mime_type,
            add_random_suffix=False,
            overwrite=False,
            token=self.private_write_token,
        )
        provider_url = str(
            result.get("url") if isinstance(result, dict) else getattr(result, "url", "")
        )
        parsed = urlsplit(provider_url)
        if f"https://{parsed.netloc.lower()}" != self.private_origin:
            raise ValueError("private Blob write resolved to the wrong store")

    def delete_public(self, path: str) -> None:
        if not self.public_delete_token:
            raise ValueError("legacy public delete token is missing")
        from vercel.blob import delete

        delete(path, token=self.public_delete_token)


def _store(args: argparse.Namespace) -> _VercelBlob:
    public_store_id = os.environ.get(args.legacy_public_store_id_env, "").strip()
    private_store_id = os.environ.get(args.private_store_id_env, "").strip()
    if not public_store_id or not private_store_id or public_store_id == private_store_id:
        raise ValueError("public/private store IDs must be nonempty and distinct")
    public_origin = _exact_origin(
        os.environ.get("LEGACY_PUBLIC_BLOB_ORIGIN", ""),
        label="legacy public Blob origin",
    )
    private_origin = _exact_origin(
        os.environ.get("PRIVATE_BLOB_ORIGIN", ""),
        label="private Blob origin",
    )
    if public_origin == private_origin:
        raise ValueError("public and private Blob origins must be distinct")
    tokens = {
        "public_read": os.environ.get(args.legacy_public_read_token_env, "").strip()
        if args.legacy_public_read_token_env
        else None,
        "public_delete": os.environ.get(args.legacy_public_delete_token_env, "").strip()
        if args.legacy_public_delete_token_env
        else None,
        "private_read": os.environ.get(args.private_read_token_env, "").strip()
        if args.private_read_token_env
        else None,
        "private_write": os.environ.get(args.private_write_token_env, "").strip()
        if args.private_write_token_env
        else None,
    }
    present = [value for value in tokens.values() if value]
    if len(present) != len(set(present)):
        raise ValueError("storage credentials must be exact-purpose and distinct")
    return _VercelBlob(
        public_origin=public_origin,
        private_origin=private_origin,
        public_read_token=tokens["public_read"],
        public_delete_token=tokens["public_delete"],
        private_read_token=tokens["private_read"],
        private_write_token=tokens["private_write"],
    )


async def _link_asset(db, row: dict[str, Any], asset_id: UUID) -> None:
    source_kind = str(row["source_kind"])
    if source_kind.startswith("order_"):
        column = {
            "order_source": "source_asset_ids",
            "order_preview": "preview_asset_ids",
            "order_final": "final_asset_ids",
        }[source_kind]
        await db.execute(
            text(
                f"""
                UPDATE orders
                SET {column} = CASE
                  WHEN COALESCE({column}, '[]'::jsonb)
                       @> jsonb_build_array(CAST(:asset_id AS text))
                  THEN COALESCE({column}, '[]'::jsonb)
                  ELSE COALESCE({column}, '[]'::jsonb)
                       || jsonb_build_array(CAST(:asset_id AS text))
                END
                WHERE id = :source_row_id
                """
            ),
            {"asset_id": asset_id, "source_row_id": row["source_row_id"]},
        )
    else:
        column = (
            "source_asset_id"
            if source_kind == "live_portrait_source"
            else "video_asset_id"
        )
        result = await db.execute(
            text(
                f"""
                UPDATE live_portrait_jobs
                SET {column} = COALESCE({column}, :asset_id)
                WHERE id = :source_row_id
                  AND ({column} IS NULL OR {column} = :asset_id)
                RETURNING {column}
                """
            ),
            {"asset_id": asset_id, "source_row_id": row["source_row_id"]},
        )
        if str(result.scalar_one_or_none()) != str(asset_id):
            raise ValueError("Live Portrait asset link conflicts with existing fact")


async def _copy_batch(db, rows, invocation, store: _VercelBlob):
    counts = {"eligible": 0, "copied": 0, "already_copied": 0}
    blockers: dict[str, int] = {}
    for row in rows:
        classification = classify_media_reference(
            source_kind=str(row["source_kind"]),
            reference=str(row["reference"]),
            approved_public_origin=store.public_origin,
        )
        if (
            not classification.startswith("PUBLIC_USER_")
            or not bool(row["owner_exists"])
            or int(row["owner_count"]) != 1
        ):
            blockers["media_reference_not_safely_migratable"] = (
                blockers.get("media_reference_not_safely_migratable", 0) + 1
            )
            continue
        counts["eligible"] += 1
        if not invocation.write:
            continue
        source_path = _reference_path(
            str(row["reference"]),
            approved_origin=store.public_origin,
        )
        content = await asyncio.to_thread(store.read_public, source_path)
        digest = hashlib.sha256(content).hexdigest()
        mime_type = _mime_type(content, str(row["source_kind"]))
        reference_hash = hashlib.sha256(
            str(row["reference"]).encode("utf-8")
        ).hexdigest()
        private_path = (
            f"legacy-migration/{row['owner_user_id']}/"
            f"{row['source_kind']}/{row['source_row_id']}/{reference_hash}"
        )
        existing = await db.execute(
            text(
                """
                SELECT id, owner_user_id, order_id, job_id, role, sha256,
                       byte_size
                FROM media_assets
                WHERE storage_provider = 'vercel' AND object_key = :object_key
                FOR UPDATE
                """
            ),
            {"object_key": private_path},
        )
        asset = existing.mappings().one_or_none()
        if asset is None:
            try:
                await asyncio.to_thread(
                    store.put_private,
                    private_path,
                    content,
                    mime_type,
                )
                counts["copied"] += 1
            except Exception:
                read_back = await asyncio.to_thread(store.read_private, private_path)
                if hashlib.sha256(read_back).hexdigest() != digest:
                    raise
                counts["already_copied"] += 1
            read_back = await asyncio.to_thread(store.read_private, private_path)
            if hashlib.sha256(read_back).hexdigest() != digest:
                raise ValueError("private Blob copy read-back checksum mismatch")
            asset_id = legacy_media_asset_id(
                source_kind=str(row["source_kind"]),
                source_row_id=row["source_row_id"],
                reference=str(row["reference"]),
            )
            await db.execute(
                text(
                    """
                    INSERT INTO media_assets (
                      id, owner_user_id, order_id, job_id, role, storage_provider,
                      object_key, sha256, mime_type, byte_size, access_level,
                      policy_version, expires_at, status
                    ) VALUES (
                      :id, :owner_user_id, :order_id, :job_id, :role, 'vercel',
                      :object_key, :sha256, :mime_type, :byte_size, 'private',
                      'legacy-migration-v1', :expires_at, 'ACTIVE'
                    )
                    """
                ),
                {
                    "id": asset_id,
                    "owner_user_id": row["owner_user_id"],
                    "order_id": (
                        row["source_row_id"]
                        if str(row["source_kind"]).startswith("order_")
                        else None
                    ),
                    "job_id": (
                        row["job_id"]
                        if str(row["source_kind"]).startswith("order_")
                        else None
                    ),
                    "role": _asset_role(str(row["source_kind"])),
                    "object_key": private_path,
                    "sha256": digest,
                    "mime_type": mime_type,
                    "byte_size": len(content),
                    "expires_at": row["expires_at"],
                },
            )
        else:
            asset_id = UUID(str(asset["id"]))
            if (
                str(asset["owner_user_id"]) != str(row["owner_user_id"])
                or str(asset["order_id"] or "") != str(
                    row["source_row_id"]
                    if str(row["source_kind"]).startswith("order_")
                    else ""
                )
                or str(asset["job_id"] or "") != str(row["job_id"] or "")
                or str(asset["role"]) != _asset_role(str(row["source_kind"]))
                or str(asset["sha256"]) != digest
                or int(asset["byte_size"]) != len(content)
            ):
                raise ValueError("existing private media fact conflicts with copied bytes")
            read_back = await asyncio.to_thread(store.read_private, private_path)
            if hashlib.sha256(read_back).hexdigest() != digest:
                raise ValueError("existing private Blob checksum mismatch")
            counts["already_copied"] += 1
        await _link_asset(db, row, asset_id)
    return counts, blockers


async def _delete_batch(db, rows, invocation, store: _VercelBlob):
    counts = {"eligible": 0, "deleted": 0, "already_deleted": 0}
    blockers: dict[str, int] = {}
    for row in rows:
        asset_id = legacy_media_asset_id(
            source_kind=str(row["source_kind"]),
            source_row_id=row["source_row_id"],
            reference=str(row["reference"]),
        )
        asset = (
            await db.execute(
                text(
                    """
                    SELECT id, owner_user_id, object_key, sha256, status
                    FROM media_assets
                    WHERE id = :asset_id
                    """
                ),
                {"asset_id": asset_id},
            )
        ).mappings().one_or_none()
        if (
            asset is None
            or str(asset["owner_user_id"]) != str(row["owner_user_id"])
            or str(asset["status"]) != "ACTIVE"
        ):
            blockers["private_fact_missing_or_inactive"] = (
                blockers.get("private_fact_missing_or_inactive", 0) + 1
            )
            continue
        private_bytes = await asyncio.to_thread(
            store.read_private,
            str(asset["object_key"]),
        )
        if hashlib.sha256(private_bytes).hexdigest() != str(asset["sha256"]):
            blockers["private_readback_checksum_mismatch"] = (
                blockers.get("private_readback_checksum_mismatch", 0) + 1
            )
            continue
        counts["eligible"] += 1
        if not invocation.write:
            continue
        source_path = _reference_path(
            str(row["reference"]),
            approved_origin=store.public_origin,
        )
        try:
            await asyncio.to_thread(store.delete_public, source_path)
            counts["deleted"] += 1
        except FileNotFoundError:
            counts["already_deleted"] += 1
    return counts, blockers


def _verify_required_dry_run(
    args: argparse.Namespace,
    invocation,
) -> None:
    if not args.required_dry_run_report:
        raise ValueError("delete write requires the bound dry-run report")
    raw = Path(args.required_dry_run_report).read_bytes()
    expected = str(args.expected_dry_run_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("delete dry-run report SHA-256 is invalid")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("delete dry-run report SHA-256 mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if (
        payload.get("tool") != "migrate_public_media"
        or payload.get("mode") != "delete-dry"
        or payload.get("passed") is not True
        or payload.get("write_performed") is not False
        or payload.get("inventory_sha256") != invocation.inventory_sha256
        or payload.get("manifest_sha256") != invocation.manifest_sha256
        or payload.get("runtime_bundle_id")
        != invocation.contract.runtime_bundle_id
        or payload.get("source_revision")
        != invocation.contract.source_revision
        or payload.get("blockers") != {}
        or (payload.get("details") or {}).get("operation")
        != "delete-old-public"
    ):
        raise ValueError("delete dry-run report is not authoritative")


async def _run(args: argparse.Namespace) -> None:
    mode = (
        "copy-write" if args.mode == "copy-and-switch" and args.write
        else "copy-dry" if args.mode == "copy-and-switch"
        else "delete-write" if args.write
        else "delete-dry"
    )
    store = _store(args)
    invocation = load_invocation(
        args,
        script_path=Path(__file__),
        mode=mode,
    )
    if mode == "delete-write":
        _verify_required_dry_run(args, invocation)
    processor = (
        (lambda db, rows, inv: _copy_batch(db, rows, inv, store))
        if args.mode == "copy-and-switch"
        else (lambda db, rows, inv: _delete_batch(db, rows, inv, store))
    )
    counts, blockers = await run_batch_migration(
        invocation,
        select_sql=_SELECT,
        process_batch=processor,
    )
    write_report_create_once(
        invocation,
        tool="migrate_public_media",
        counts=counts,
        blockers=blockers,
        passed=not blockers,
        extra={
            "operation": args.mode,
            "old_reference_preserved_on_copy_failure": True,
            "public_private_store_distinct": True,
        },
    )
    if blockers:
        raise ValueError("public media migration has blocking rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(
        parser,
        modes=("copy-and-switch", "delete-old-public"),
    )
    parser.add_argument(
        "--legacy-public-store-id-env",
        default="LEGACY_PUBLIC_BLOB_STORE_ID",
    )
    parser.add_argument("--legacy-public-read-token-env")
    parser.add_argument("--legacy-public-delete-token-env")
    parser.add_argument("--private-store-id-env", default="PRIVATE_BLOB_STORE_ID")
    parser.add_argument("--private-read-token-env")
    parser.add_argument("--private-write-token-env")
    parser.add_argument("--required-dry-run-report")
    parser.add_argument("--expected-dry-run-sha256")
    args = parser.parse_args()
    if args.mode == "copy-and-switch":
        args.legacy_public_read_token_env = (
            args.legacy_public_read_token_env or "LEGACY_PUBLIC_BLOB_READ_TOKEN"
        )
        args.private_write_token_env = (
            args.private_write_token_env or "PRIVATE_BLOB_WRITE_TOKEN"
        )
    else:
        args.legacy_public_delete_token_env = (
            args.legacy_public_delete_token_env or "LEGACY_PUBLIC_BLOB_DELETE_TOKEN"
        )
        args.private_read_token_env = (
            args.private_read_token_env or "PRIVATE_BLOB_READ_TOKEN"
        )
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
