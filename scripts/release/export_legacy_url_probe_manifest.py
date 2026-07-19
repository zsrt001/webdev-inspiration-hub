#!/usr/bin/env python3
"""Export raw legacy URLs only to a mode-0600 ephemeral probe manifest."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.services.production_inventory_service import hmac_identifier  # noqa: E402
from scripts.release.build_manifest import canonical_manifest_bytes, validate_manifest  # noqa: E402
from scripts.release.inventory_production import source_database_identity  # noqa: E402
from scripts.release.migrate_public_media import legacy_media_asset_id  # noqa: E402
from scripts.release.verify_inventory_signature import verify_inventory_evidence  # noqa: E402


_REFERENCE_SQL = """
SELECT source_kind, source_row_id, owner_user_id, reference
FROM (
  SELECT 'order_source'::text AS source_kind, o.id AS source_row_id,
         o.user_id AS owner_user_id, value #>> '{}' AS reference
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
) refs
ORDER BY source_kind, source_row_id::text, reference
"""


def _ephemeral_output(path: Path) -> Path:
    runner_temp = os.environ.get("RUNNER_TEMP", "").strip()
    if not runner_temp:
        raise ValueError("RUNNER_TEMP is required for raw probe material")
    root = Path(runner_temp).resolve()
    output = path.resolve()
    if output == root or root not in output.parents:
        raise ValueError("raw probe manifest must stay inside RUNNER_TEMP")
    workspace = ROOT.resolve()
    if output == workspace or workspace in output.parents:
        raise ValueError("raw probe manifest must not be written to the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


async def _run(args: argparse.Namespace) -> int:
    database_url = os.environ.get(args.database_url_env, "").strip()
    key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
    if not database_url or len(key) < 32:
        raise ValueError("read-only database URL and inventory HMAC key are required")
    inventory = verify_inventory_evidence(
        report_path=Path(args.inventory_report),
        signature_path=Path(args.inventory_signature),
        expected_sha256=args.expected_inventory_sha256,
        hmac_key=key,
    )
    expected_identity = hmac_identifier(
        key,
        "source_database",
        source_database_identity(database_url),
    )
    if inventory["source_database_identity_hmac_sha256"] != expected_identity:
        raise ValueError("probe database identity does not match inventory")
    manifest_raw = Path(args.release_manifest).read_bytes()
    expected_manifest_sha256 = str(
        args.expected_manifest_sha256 or ""
    ).strip().lower()
    if (
        len(expected_manifest_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_manifest_sha256)
        or hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256
    ):
        raise ValueError("release manifest SHA-256 mismatch")
    manifest = validate_manifest(json.loads(manifest_raw.decode("utf-8")))
    if canonical_manifest_bytes(manifest) != manifest_raw:
        raise ValueError("release manifest is not canonical")
    if manifest["release_role"] != "COMMERCIAL_7A":
        raise ValueError("legacy probe export requires COMMERCIAL_7A")

    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    entries_by_hmac: dict[str, dict[str, str]] = {}
    try:
        async with factory() as db:
            async with db.begin():
                await db.execute(text("SET TRANSACTION READ ONLY"))
                rows = (await db.execute(text(_REFERENCE_SQL))).mappings().all()
                for row in rows:
                    reference = str(row["reference"])
                    asset_id = legacy_media_asset_id(
                        source_kind=str(row["source_kind"]),
                        source_row_id=row["source_row_id"],
                        reference=reference,
                    )
                    asset = (
                        await db.execute(
                            text(
                                """
                                SELECT sha256, status
                                FROM media_assets
                                WHERE id = :asset_id
                                """
                            ),
                            {"asset_id": asset_id},
                        )
                    ).mappings().one_or_none()
                    if asset is None or str(asset["status"]) != "ACTIVE":
                        raise ValueError("legacy URL lacks an ACTIVE private asset fact")
                    url_hmac = hmac_identifier(
                        key,
                        "legacy_url_probe",
                        reference,
                    )
                    entry = {
                        "url": reference,
                        "url_hmac_sha256": url_hmac,
                        "expected_old_sha256": str(asset["sha256"]),
                    }
                    existing = entries_by_hmac.get(url_hmac)
                    if existing is not None and existing != entry:
                        raise ValueError(
                            "duplicate legacy URL has conflicting private evidence"
                        )
                    entries_by_hmac[url_hmac] = entry
    finally:
        await engine.dispose()
    entries = [
        entries_by_hmac[key]
        for key in sorted(entries_by_hmac)
    ]
    unsigned = {
        "schema": "vowpic.legacy-url-probe-manifest.v1",
        "inventory_sha256": args.expected_inventory_sha256.lower(),
        "manifest_sha256": expected_manifest_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    payload = {**unsigned, "signature": f"hmac-sha256:{signature}"}
    output = _ephemeral_output(Path(args.output))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(output, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR)
    print(json.dumps({"entry_count": len(entries)}, sort_keys=True))
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="PRODUCTION_READ_ONLY_DATABASE_URL",
    )
    parser.add_argument("--hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--inventory-report", required=True)
    parser.add_argument("--inventory-signature", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--release-manifest", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
