#!/usr/bin/env python3
"""Create a create-once, redacted inventory evidence report."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from app.services.production_inventory_service import build_inventory_report  # noqa: E402


NOT_RUN_EXIT = 3


def _write_create_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


async def _run(args: argparse.Namespace, database_url: str, hmac_key: bytes) -> dict[str, str]:
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            async with db.begin():
                report = await build_inventory_report(db, hmac_key)
        payload = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        output = Path(args.output)
        manifest = output.parent / "manifest.sha256"
        _write_create_once(output, payload)
        try:
            _write_create_once(manifest, f"{digest}  {output.name}\n".encode("ascii"))
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return {"output": str(output), "sha256": digest, "schema_revision": report.schema_revision}
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PRODUCTION_READ_ONLY_DATABASE_URL")
    parser.add_argument("--hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env, "").strip()
    hmac_key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
    if not database_url or not hmac_key:
        print("NOT_RUN: protected read-only database URL and inventory HMAC key are required", file=sys.stderr)
        return NOT_RUN_EXIT
    if len(hmac_key) < 32:
        print("NOT_RUN: inventory HMAC key must contain at least 32 bytes", file=sys.stderr)
        return NOT_RUN_EXIT
    result = asyncio.run(_run(args, database_url, hmac_key))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
