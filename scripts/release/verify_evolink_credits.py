#!/usr/bin/env python3
"""Verify that the protected EvoLink key can fund one Preview image proof.

The report intentionally records only threshold decisions.  It never persists
the API key or the account's exact financial balance.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://api.evolink.ai/v1/credits"
DEFAULT_MINIMUM_CREDITS = Decimal("10")
MAX_RESPONSE_BYTES = 64 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"EvoLink credits field is invalid: {field}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"EvoLink credits field is invalid: {field}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"EvoLink credits field is invalid: {field}")
    return parsed


def evaluate_credits(
    payload: dict[str, Any],
    *,
    minimum_credits: Decimal = DEFAULT_MINIMUM_CREDITS,
) -> dict[str, Any]:
    minimum = _decimal(minimum_credits, field="minimum_credits")
    if minimum <= 0:
        raise ValueError("minimum EvoLink credits must be positive")
    if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
        raise ValueError("EvoLink credits response is invalid")
    data = payload["data"]
    token = data.get("token")
    user = data.get("user")
    if not isinstance(token, dict) or not isinstance(user, dict):
        raise ValueError("EvoLink credits response is invalid")
    unlimited = token.get("unlimited_credits")
    if type(unlimited) is not bool:
        raise ValueError("EvoLink credits field is invalid: token.unlimited_credits")
    token_remaining = _decimal(
        token.get("remaining_credits"), field="token.remaining_credits"
    )
    user_remaining = _decimal(
        user.get("remaining_credits"), field="user.remaining_credits"
    )
    token_threshold_met = bool(unlimited or token_remaining >= minimum)
    user_threshold_met = bool(user_remaining >= minimum)
    return {
        "schema": "vowpic.evolink-credit-preflight.v1",
        "passed": token_threshold_met and user_threshold_met,
        "endpoint": ENDPOINT,
        "minimum_credits": format(minimum, "f"),
        "token_unlimited": unlimited,
        "token_threshold_met": token_threshold_met,
        "user_threshold_met": user_threshold_met,
    }


def load_minimum_credits(path: Path) -> Decimal:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        providers = document["providers"]
        evolink = providers["evolink"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("EvoLink provider capability contract is invalid") from exc
    if (
        document.get("schema") != "vowpic.provider-capabilities.v1"
        or not isinstance(evolink, dict)
        or evolink.get("credit_endpoint") != "/v1/credits"
    ):
        raise ValueError("EvoLink provider capability contract is invalid")
    minimum = _decimal(
        evolink.get("acceptance_minimum_credits"),
        field="acceptance_minimum_credits",
    )
    if minimum <= 0:
        raise ValueError("minimum EvoLink credits must be positive")
    return minimum


def fetch_credits(
    api_key: str,
    *,
    timeout_seconds: float = 15.0,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("EvoLink API key is required")
    request = Request(
        ENDPOINT,
        method="GET",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "VowPic-EvoLink-Credit-Preflight/1.0",
        },
    )
    open_request = opener or build_opener(_RejectRedirects()).open
    try:
        with open_request(request, timeout=max(1.0, float(timeout_seconds))) as response:
            status = int(response.getcode())
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"EvoLink credits endpoint rejected HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError("EvoLink credits endpoint is unavailable") from exc
    if status != 200:
        raise ValueError(f"EvoLink credits endpoint rejected HTTP {status}")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("EvoLink credits response is too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("EvoLink credits response is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("EvoLink credits response is invalid")
    return payload


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-env", default="EVOLINK_API_KEY")
    parser.add_argument("--capabilities", default="release/provider-capabilities.json")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = evaluate_credits(
        fetch_credits(
            os.environ.get(args.api_key_env, ""),
            timeout_seconds=args.timeout_seconds,
        ),
        minimum_credits=load_minimum_credits(Path(args.capabilities)),
    )
    _write_report(Path(args.output), report)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if not report["passed"]:
        raise SystemExit(
            "EvoLink credits are below the protected Preview proof threshold"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
