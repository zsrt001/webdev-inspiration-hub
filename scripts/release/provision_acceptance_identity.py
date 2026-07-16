#!/usr/bin/env python3
"""Provision hash-only, one-time acceptance identities from a protected secret file."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.services.acceptance_identity_service import create_acceptance_binding  # noqa: E402


def _subjects(path: Path) -> list[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("subjects file must be a non-empty JSON array")
    result: list[tuple[str, str]] = []
    for item in payload:
        if isinstance(item, str):
            provider, subject = "google", item
        elif isinstance(item, dict) and set(item) == {"provider", "subject"}:
            provider, subject = str(item["provider"]), str(item["subject"])
        else:
            raise ValueError("each subject must be a string or {provider, subject}")
        if not provider.strip() or not subject.strip():
            raise ValueError("provider and subject must be non-empty")
        if provider.strip().lower() != "google":
            raise ValueError("only verified Google Supabase subjects may be provisioned")
        result.append((provider.strip().lower(), subject.strip()))
    if len(set(result)) != len(result):
        raise ValueError("subjects file contains duplicates")
    return result


async def _run(args: argparse.Namespace) -> int:
    database_url = os.environ.get(args.database_url_env, "").strip()
    hmac_key = os.environ.get(args.hmac_key_env, "").strip()
    approval = os.environ.get(args.approval_id_env, "").strip()
    if not database_url or not hmac_key or not approval:
        raise ValueError("database, HMAC key, and protected approval environment variables are required")
    if len(hmac_key) < 32:
        raise ValueError("acceptance identity HMAC key must contain at least 32 characters")
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=args.ttl_seconds)
    entries = _subjects(Path(args.subjects_file))
    try:
        async with session_factory() as db:
            async with db.begin():
                for provider, subject in entries:
                    await create_acceptance_binding(
                        db,
                        provider=provider,
                        subject=subject,
                        environment=args.environment,
                        deployment_id=args.deployment_id,
                        expires_at=expires_at,
                        actor=args.actor,
                        reason=f"{args.reason}; approval={approval}",
                        hmac_key=hmac_key,
                        now=now,
                    )
    finally:
        await engine.dispose()
    print(json.dumps({"created": len(entries), "environment": args.environment, "deployment_id": args.deployment_id}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects-file", required=True)
    parser.add_argument("--environment", required=True, choices=("preview", "production"))
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=7200, choices=range(1, 86401), metavar="1..86400")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--database-url-env", default="ACCEPTANCE_DATABASE_URL")
    parser.add_argument("--hmac-key-env", default="ACCEPTANCE_IDENTITY_HMAC_KEY")
    parser.add_argument("--approval-id-env", default="ACCEPTANCE_IDENTITY_APPROVAL_ID")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
