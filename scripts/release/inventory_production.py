#!/usr/bin/env python3
"""Create a create-once, redacted inventory evidence report."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


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


def _canonical_report_bytes(report: object) -> bytes:
    payload = report.model_dump(mode="json", by_alias=True)
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def source_database_identity(database_url: str) -> str:
    normalized = str(database_url or "").strip()
    if normalized.startswith("postgresql+asyncpg://"):
        normalized = "postgresql://" + normalized[len("postgresql+asyncpg://"):]
    parsed = urlsplit(normalized)
    host = str(parsed.hostname or "").strip().lower()
    login = unquote(parsed.username or "").strip().lower()
    database = unquote((parsed.path or "/postgres").lstrip("/") or "postgres").lower()
    port = parsed.port or 5432
    if parsed.scheme not in {"postgresql", "postgres"} or not host or not login:
        raise ValueError("inventory database URL is invalid")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
        if not project_ref:
            raise ValueError("Supabase direct URL is missing its project reference")
        return f"supabase:{project_ref}:0:{database}"
    if "pooler.supabase." in host:
        if "." not in login:
            raise ValueError("Supabase pooler login is missing its project reference")
        project_ref = login.rsplit(".", 1)[1]
        if not project_ref:
            raise ValueError("Supabase pooler login is missing its project reference")
        return f"supabase:{project_ref}:0:{database}"
    return f"postgresql:{host}:{port}:{database}"


async def _run(args: argparse.Namespace, database_url: str, hmac_key: bytes) -> dict[str, str]:
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            async with db.begin():
                report = await build_inventory_report(
                    db,
                    hmac_key,
                    source_database_identity=source_database_identity(database_url),
                )
        payload = _canonical_report_bytes(report)
        digest = hashlib.sha256(payload).hexdigest()
        output = Path(args.output)
        signature_output = Path(args.signature_output)
        signature = (
            "hmac-sha256:"
            + hmac.new(hmac_key, payload, hashlib.sha256).hexdigest()
            + "\n"
        ).encode("ascii")
        _write_create_once(output, payload)
        try:
            _write_create_once(signature_output, signature)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return {
            "output": str(output),
            "signature_output": str(signature_output),
            "sha256": digest,
            "schema_revision": report.schema_revision,
            "source_database_identity_hmac_sha256": (
                report.source_database_identity_hmac_sha256
            ),
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PRODUCTION_READ_ONLY_DATABASE_URL")
    parser.add_argument("--hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--output", required=True)
    parser.add_argument("--signature-output")
    args = parser.parse_args()
    if not args.signature_output:
        args.signature_output = f"{args.output}.sig"
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
