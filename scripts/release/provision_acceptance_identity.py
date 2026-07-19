#!/usr/bin/env python3
"""Provision hash-only, one-time acceptance identities from a protected secret file."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
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
from sqlalchemy import select  # noqa: E402

from app.models.acceptance_identity_binding import AcceptanceIdentityBinding  # noqa: E402
from app.services.acceptance_identity_service import (  # noqa: E402
    compute_subject_hmac,
    create_acceptance_binding,
)


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
    subjects_path = args.subjects_file
    if args.subject_file_env:
        subjects_path = os.environ.get(args.subject_file_env, "").strip()
    if not subjects_path:
        raise ValueError("protected acceptance subjects file is required")
    entries = _subjects(Path(subjects_path))
    bindings: list[AcceptanceIdentityBinding] = []
    effective_expiry: datetime | None = None
    try:
        async with session_factory() as db:
            async with db.begin():
                for provider, subject in entries:
                    subject_hmac = compute_subject_hmac(hmac_key, provider, subject)
                    result = await db.execute(
                        select(AcceptanceIdentityBinding).where(
                            AcceptanceIdentityBinding.environment == args.environment,
                            AcceptanceIdentityBinding.deployment_id == args.deployment_id,
                            AcceptanceIdentityBinding.provider == provider,
                            AcceptanceIdentityBinding.subject_hmac == subject_hmac,
                        )
                    )
                    binding = result.scalar_one_or_none()
                    reason = f"{args.reason}; approval={approval}"
                    if binding is None:
                        binding_expiry = effective_expiry or expires_at
                        binding = await create_acceptance_binding(
                            db,
                            provider=provider,
                            subject=subject,
                            environment=args.environment,
                            deployment_id=args.deployment_id,
                            expires_at=binding_expiry,
                            actor=args.actor,
                            reason=reason,
                            hmac_key=hmac_key,
                            now=now,
                        )
                    elif (
                        binding.actor != args.actor
                        or binding.reason != reason
                        or binding.expires_at <= now
                        or binding.expires_at > expires_at
                        or binding.consumed_at is not None
                        or binding.revoked_at is not None
                    ):
                        raise ValueError("existing acceptance binding drifted or was already used")
                    if effective_expiry is None:
                        effective_expiry = binding.expires_at
                    elif binding.expires_at != effective_expiry:
                        raise ValueError("acceptance binding expiries are inconsistent")
                    bindings.append(binding)
    finally:
        await engine.dispose()
    if effective_expiry is None:
        raise ValueError("acceptance bindings were not created")
    report = {
        "schema": "vowpic.acceptance-identity-bindings.v1",
        "passed": True,
        "count": len(bindings),
        "environment": args.environment,
        "deployment_id": args.deployment_id,
        "expires_at": effective_expiry.isoformat(),
        "binding_id_hashes": sorted(
            hashlib.sha256(str(binding.id).encode("utf-8")).hexdigest()
            for binding in bindings
        ),
        "subject_hmac_sha256": sorted(
            hashlib.sha256(binding.subject_hmac.encode("utf-8")).hexdigest()
            for binding in bindings
        ),
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects-file")
    parser.add_argument("--subject-file-env")
    parser.add_argument("--environment", required=True, choices=("preview", "production"))
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=7200, choices=range(1, 86401), metavar="1..86400")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--database-url-env", default="ACCEPTANCE_DATABASE_URL")
    parser.add_argument("--hmac-key-env", default="ACCEPTANCE_IDENTITY_HMAC_KEY")
    parser.add_argument("--approval-id-env", default="ACCEPTANCE_IDENTITY_APPROVAL_ID")
    parser.add_argument("--output")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
