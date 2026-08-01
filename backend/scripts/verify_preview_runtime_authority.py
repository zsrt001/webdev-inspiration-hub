#!/usr/bin/env python3
"""Verify the Preview Vercel runtime can read the authoritative feature flags."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from sqlalchemy import text

from app.core.database import engine


SCHEMA = "vowpic.preview-runtime-authority-proof.v1"
EXPECTED_CAPABILITIES = {
    "google_auth",
    "authenticated_upload",
    "generation",
    "credit_pack_checkout",
    "subscription_billing",
    "private_download",
    "partner_invite",
}


def validate_facts(facts: dict[str, object]) -> dict[str, object]:
    capabilities = set(facts.get("capabilities") or [])
    if facts.get("current_user") != "vowpic_app_runtime":
        raise ValueError("Preview runtime database identity is invalid")
    if facts.get("runtime_member") is not True:
        raise ValueError("Preview runtime database identity lacks the runtime role")
    if facts.get("flags_select") is not True or facts.get("flags_update") is not False:
        raise ValueError("Preview runtime feature-flag privileges are invalid")
    if capabilities != EXPECTED_CAPABILITIES:
        raise ValueError("Preview runtime cannot read the complete feature-flag authority")
    return {
        "schema": SCHEMA,
        "passed": True,
        "current_user": "vowpic_app_runtime",
        "runtime_member": True,
        "flags_select": True,
        "flags_update": False,
        "preview_capability_count": len(capabilities),
    }


async def read_facts() -> dict[str, object]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT current_user,
                           pg_has_role(current_user, 'vowpic_runtime', 'MEMBER')
                             AS runtime_member,
                           has_table_privilege(
                             current_user, 'public.ops_feature_flags', 'SELECT'
                           ) AS flags_select,
                           has_table_privilege(
                             current_user, 'public.ops_feature_flags', 'UPDATE'
                           ) AS flags_update
                    """
                )
            )
        ).mappings().one()
        capability_rows = (
            await connection.execute(
                text(
                    """
                    SELECT capability
                    FROM public.ops_feature_flags
                    WHERE environment = 'preview'
                    ORDER BY capability
                    """
                )
            )
        ).scalars().all()
    return {**dict(row), "capabilities": list(capability_rows)}


async def prove(timeout_seconds: float) -> dict[str, object]:
    try:
        facts = await asyncio.wait_for(read_facts(), timeout=timeout_seconds)
        return validate_facts(facts)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    try:
        report = asyncio.run(prove(args.timeout_seconds))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
            handle.write("\n")
        print(json.dumps({"schema": SCHEMA, "passed": True}))
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
