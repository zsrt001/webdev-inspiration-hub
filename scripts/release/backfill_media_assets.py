#!/usr/bin/env python3
"""Classify every legacy media reference before any byte is copied or deleted."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

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


_SELECT = """
WITH refs AS (
  SELECT
    'order_source'::text AS source_kind,
    o.id AS source_row_id,
    o.user_id AS owner_user_id,
    value #>> '{}' AS reference
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.source_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'order_preview', o.id, o.user_id, value #>> '{}'
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.preview_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'order_final', o.id, o.user_id, value #>> '{}'
  FROM orders o
  CROSS JOIN LATERAL jsonb_path_query(
    COALESCE(o.final_image_urls, '{}'::jsonb),
    '$.** ? (@.type() == "string")'
  ) value
  UNION ALL
  SELECT 'live_portrait_source', lp.id, lp.user_id, lp.source_image_url
  FROM live_portrait_jobs lp WHERE NULLIF(lp.source_image_url, '') IS NOT NULL
  UNION ALL
  SELECT 'live_portrait_video', lp.id, lp.user_id, lp.video_url
  FROM live_portrait_jobs lp WHERE NULLIF(lp.video_url, '') IS NOT NULL
  UNION ALL
  SELECT 'user_avatar', u.id, u.id, u.avatar_url
  FROM users u WHERE NULLIF(u.avatar_url, '') IS NOT NULL
), ref_counts AS (
  SELECT reference, count(*)::int AS global_reference_count,
         count(DISTINCT owner_user_id)::int AS owner_count
  FROM refs
  GROUP BY reference
), normalized AS (
  SELECT
    source_kind || ':' || source_row_id::text || ':' ||
      md5(reference) AS id,
    source_kind,
    source_row_id,
    owner_user_id,
    reference,
    EXISTS (SELECT 1 FROM users u WHERE u.id = refs.owner_user_id) AS owner_exists,
    ref_counts.global_reference_count,
    ref_counts.owner_count
  FROM refs
  JOIN ref_counts USING (reference)
)
SELECT *
FROM normalized
WHERE (:after IS NULL OR id > :after)
ORDER BY id
LIMIT :batch_size
"""


def _origin(value: str) -> str:
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
        raise ValueError("legacy public Blob origin must be an exact HTTPS origin")
    return f"https://{parsed.netloc.lower()}"


def classify_media_reference(
    *,
    source_kind: str,
    reference: str,
    approved_public_origin: str,
) -> str:
    parsed = urlsplit(str(reference or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return "UNKNOWN"
    origin = f"https://{parsed.netloc.lower()}"
    lower_path = parsed.path.lower()
    query = parsed.query.lower()
    if any(
        marker in query
        for marker in (
            "token=",
            "signature=",
            "x-amz-signature=",
            "x-amz-credential=",
        )
    ) or any(marker in lower_path for marker in ("/private/", "/protected/")):
        return "PRIVATE_EXISTING"
    if origin != approved_public_origin:
        return "EXTERNAL_REVIEW"
    if any(
        marker in lower_path
        for marker in (
            "/marketing/",
            "/templates/",
            "/scenes/",
            "/outfits/",
        )
    ):
        return "PUBLIC_PRODUCT_ASSET"
    if any(marker in lower_path for marker in ("/tmp/", "/temp/", "/debug/")):
        return "TEMPORARY_DEBUG"
    mapping = {
        "order_source": "PUBLIC_USER_SOURCE",
        "order_preview": "PUBLIC_USER_CANDIDATE",
        "order_final": "PUBLIC_USER_FINAL",
        "live_portrait_source": "PUBLIC_USER_SOURCE",
        "live_portrait_video": "PUBLIC_USER_FINAL",
    }
    if source_kind == "user_avatar":
        return "EXTERNAL_REVIEW"
    return mapping.get(source_kind, "UNKNOWN")


async def _process_batch(db, rows, invocation):
    _ = db
    origin = _origin(os.environ.get("LEGACY_PUBLIC_BLOB_ORIGIN", ""))
    counts: dict[str, int] = {}
    blockers: dict[str, int] = {}
    for row in rows:
        classification = classify_media_reference(
            source_kind=str(row["source_kind"]),
            reference=str(row["reference"]),
            approved_public_origin=origin,
        )
        counts[classification] = counts.get(classification, 0) + 1
        if not bool(row["owner_exists"]):
            blockers["unknown_owner"] = blockers.get("unknown_owner", 0) + 1
        if int(row["owner_count"]) > 1:
            blockers["shared_cross_owner_object"] = (
                blockers.get("shared_cross_owner_object", 0) + 1
            )
        if classification in {"UNKNOWN", "EXTERNAL_REVIEW"}:
            blockers["unapproved_reference"] = (
                blockers.get("unapproved_reference", 0) + 1
            )
    return counts, blockers


async def _run(args: argparse.Namespace) -> None:
    public_store_id = os.environ.get("LEGACY_PUBLIC_BLOB_STORE_ID", "").strip()
    private_store_id = os.environ.get("PRIVATE_BLOB_STORE_ID", "").strip()
    if not public_store_id or not private_store_id:
        raise ValueError("public and private Blob store IDs are required")
    if public_store_id == private_store_id:
        raise ValueError("public and private Blob stores must be distinct")
    _origin(os.environ.get("LEGACY_PUBLIC_BLOB_ORIGIN", ""))
    invocation = load_invocation(args, script_path=Path(__file__))
    counts, blockers = await run_batch_migration(
        invocation,
        select_sql=_SELECT,
        process_batch=_process_batch,
    )
    expected_asset_references = int(
        invocation.inventory["objects"].get("total_references", 0)
    ) - int(
        invocation.inventory["objects"].get("non_asset_url_references", 0)
    )
    classified_references = sum(counts.values())
    if classified_references != expected_asset_references:
        blockers["media_inventory_count_drift"] = 1
    write_report_create_once(
        invocation,
        tool="backfill_media_assets",
        counts=counts,
        blockers=blockers,
        passed=not blockers,
        extra={
            "public_store_distinct_from_private": True,
            "raw_reference_count_in_report": 0,
            "classified_reference_count": classified_references,
            "expected_asset_reference_count": expected_asset_references,
        },
    )
    if blockers:
        raise ValueError("media reference classification has blocking rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    args = parser.parse_args()
    return safe_main(lambda: asyncio.run(_run(args)))


if __name__ == "__main__":
    raise SystemExit(main())
