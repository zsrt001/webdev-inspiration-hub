#!/usr/bin/env python3
"""Verify a canonical Production inventory and its detached HMAC signature."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for location in (ROOT, BACKEND):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from app.services.production_inventory_service import (  # noqa: E402
    ProductionInventoryReport,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def _parse_timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("inventory generated_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("inventory generated_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def verify_inventory_evidence(
    *,
    report_path: Path,
    signature_path: Path,
    expected_sha256: str,
    hmac_key: bytes,
    now: datetime | None = None,
    maximum_age_seconds: int = 900,
    expected_schema_revision: str | None = None,
) -> dict[str, Any]:
    expected = str(expected_sha256 or "").strip().lower()
    if not _SHA256.fullmatch(expected):
        raise ValueError("expected inventory SHA-256 is invalid")
    if len(hmac_key) < 32:
        raise ValueError("inventory HMAC key must contain at least 32 bytes")
    raw = report_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("inventory SHA-256 mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("inventory report is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("inventory report must be a JSON object")
    if canonical_json_bytes(payload) != raw:
        raise ValueError("inventory report is not canonical JSON")
    ProductionInventoryReport.model_validate(payload)
    signature_text = signature_path.read_text(encoding="ascii").strip().lower()
    match = _SIGNATURE.fullmatch(signature_text)
    expected_signature = hmac.new(hmac_key, raw, hashlib.sha256).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), expected_signature):
        raise ValueError("inventory detached HMAC signature mismatch")
    if payload.get("schema") != "vowpic.production-inventory.v2":
        raise ValueError("inventory schema is not supported")
    revision = str(payload.get("schema_revision") or "").strip()
    if expected_schema_revision and revision != expected_schema_revision:
        raise ValueError("inventory schema revision mismatch")
    source_identity = str(
        payload.get("source_database_identity_hmac_sha256") or ""
    ).strip()
    if not _SHA256.fullmatch(source_identity):
        raise ValueError("inventory source database identity is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created = _parse_timestamp(payload.get("generated_at"))
    maximum_age = timedelta(seconds=max(1, int(maximum_age_seconds)))
    if created > current + timedelta(seconds=60):
        raise ValueError("inventory report is from the future")
    if current - created > maximum_age:
        raise ValueError("inventory report is stale")
    return payload


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-report", required=True)
    parser.add_argument("--inventory-signature", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--hmac-key-env", default="INVENTORY_HMAC_KEY")
    parser.add_argument("--maximum-age-seconds", type=int, default=900)
    parser.add_argument("--expected-schema-revision")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
        payload = verify_inventory_evidence(
            report_path=Path(args.inventory_report),
            signature_path=Path(args.inventory_signature),
            expected_sha256=args.expected_inventory_sha256,
            hmac_key=key,
            maximum_age_seconds=args.maximum_age_seconds,
            expected_schema_revision=args.expected_schema_revision,
        )
        result = {
            "schema": "vowpic.inventory-verification.v1",
            "passed": True,
            "inventory_sha256": args.expected_inventory_sha256.lower(),
            "schema_revision": payload["schema_revision"],
            "source_database_identity_hmac_sha256": (
                payload["source_database_identity_hmac_sha256"]
            ),
        }
        if args.output:
            _write_create_once(Path(args.output), result)
        else:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
