#!/usr/bin/env python3
"""Validate the provider behaviors VowPic actually implements.

This is a static adapter-policy check. It does not pretend that EvoLink offers
idempotent submission lookup, and it does not bind provider support to a source
commit. Real credentials and behavior are verified in protected Preview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "evolink": {
        "submit_endpoint": "/v1/images/generations",
        "task_query_endpoint": "/v1/tasks/{task_id}",
        "ambiguous_submission_policy": "hold_without_resubmit",
        "official_host": "docs.evolink.ai",
        "implemented_by": {
            "backend/app/services/evolink_service.py",
            "backend/app/services/generation_attempt_service.py",
            "backend/app/services/evolink_reconciliation_service.py",
        },
    },
    "creem": {
        "cancel_endpoint": "/v1/subscriptions/{subscription_id}/cancel",
        "refund_confirmation": "refund.created_webhook",
        "subscription_events": {
            "subscription.paid",
            "subscription.scheduled_cancel",
        },
        "official_host": "docs.creem.io",
        "implemented_by": {
            "backend/app/services/creem_event_service.py",
            "backend/app/services/payment_service.py",
            "backend/app/services/subscription_service.py",
        },
    },
}


def _valid_official_url(value: Any, expected_host: str) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == expected_host
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and bool(parsed.path.strip("/"))
        and not parsed.query
        and not parsed.fragment
    )


def validate_provider_capabilities(document: dict[str, Any]) -> dict[str, list[str]]:
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "providers"}
        or document.get("schema") != "vowpic.provider-capabilities.v1"
        or not isinstance(document.get("providers"), dict)
        or set(document["providers"]) != set(EXPECTED)
    ):
        raise ValueError("provider capability document is invalid")

    result: dict[str, list[str]] = {}
    for provider, expected in EXPECTED.items():
        entry = document["providers"][provider]
        if not isinstance(entry, dict):
            raise ValueError(f"provider capability is invalid: {provider}")
        for key, value in expected.items():
            if key in {"official_host", "implemented_by", "subscription_events"}:
                continue
            if entry.get(key) != value:
                raise ValueError(f"provider capability mismatch: {provider}.{key}")
        if provider == "creem" and set(entry.get("subscription_events") or ()) != expected["subscription_events"]:
            raise ValueError("provider capability mismatch: creem.subscription_events")
        sources = entry.get("official_sources")
        if (
            not isinstance(sources, list)
            or not sources
            or len(sources) != len(set(sources))
            or not all(_valid_official_url(url, str(expected["official_host"])) for url in sources)
        ):
            raise ValueError(f"provider official sources are invalid: {provider}")
        implementations = entry.get("implemented_by")
        if not isinstance(implementations, list) or set(implementations) != expected["implemented_by"]:
            raise ValueError(f"provider implementation list is invalid: {provider}")
        for relative in implementations:
            path = (ROOT / relative).resolve()
            try:
                path.relative_to(ROOT)
            except ValueError as exc:
                raise ValueError("provider implementation path escapes the repository") from exc
            if not path.is_file():
                raise ValueError(f"provider implementation is missing: {relative}")
        result[provider] = list(sources)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", default="release/provider-capabilities.json")
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.expected_source_sha and not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_sha.strip().lower()):
        raise SystemExit("expected source SHA is invalid")
    path = Path(args.capabilities)
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    sources = validate_provider_capabilities(document)
    payload = {
        "schema": "vowpic.provider-capability-check.v1",
        "passed": True,
        "provider_capabilities_sha256": hashlib.sha256(raw).hexdigest(),
        "official_sources": sources,
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
