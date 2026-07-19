#!/usr/bin/env python3
"""Fail before Production effects unless every commercial Provider fact is verified."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


REQUIRED = {
    "CREEM_REFUND_CREATION",
    "CREEM_SUBSCRIPTION_PAID_TRANSACTION",
    "CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION",
    "EVOLINK_SUBMISSION_RECONCILIATION",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")


def validate_provider_readiness(document: dict[str, Any]) -> dict[str, str]:
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "contracts"}
        or document.get("schema") != "vowpic.provider-contracts.v1"
        or not isinstance(document.get("contracts"), dict)
        or not REQUIRED.issubset(document["contracts"])
    ):
        raise ValueError("commercial Provider contract document is invalid")
    evidence: dict[str, str] = {}
    for name in sorted(REQUIRED):
        entry = document["contracts"][name]
        source = urlsplit(str(entry.get("official_source_url") or ""))
        if (
            not isinstance(entry, dict)
            or entry.get("state") != "VERIFIED"
            or not SHA40.fullmatch(str(entry.get("tested_source_sha") or ""))
            or not SHA64.fullmatch(str(entry.get("official_contract_sha256") or ""))
            or not SHA64.fullmatch(str(entry.get("endpoint_schema_sha256") or ""))
            or not SHA64.fullmatch(str(entry.get("test_evidence_sha256") or ""))
            or source.scheme != "https"
            or not source.hostname
            or source.username
            or source.password
        ):
            raise ValueError(f"commercial Provider contract is not VERIFIED: {name}")
        evidence[name] = str(entry["test_evidence_sha256"])
    refund_url = urlsplit(
        str(document["contracts"]["CREEM_REFUND_CREATION"]["official_source_url"])
    )
    if (
        not refund_url.hostname
        or not refund_url.hostname.endswith("creem.io")
        or "/api-reference/endpoint/" not in refund_url.path
        or "refund" not in refund_url.path.lower()
    ):
        raise ValueError("Creem refund creation lacks an official API endpoint")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source_sha = str(args.expected_source_sha).strip().lower()
        if not SHA40.fullmatch(source_sha):
            raise ValueError("commercial source SHA is invalid")
        contract_path = Path(args.contract)
        raw = contract_path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
        evidence = validate_provider_readiness(document)
        output = {
            "schema": "vowpic.commercial-provider-readiness.v1",
            "passed": True,
            "source_sha": source_sha,
            "provider_contract_sha256": hashlib.sha256(raw).hexdigest(),
            "evidence_sha256": evidence,
        }
        Path(args.output).write_text(
            json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
