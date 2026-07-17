"""Bounded HTTP and read-only database probes for edge lockdown handoff."""

from __future__ import annotations

import time
from typing import Any

import httpx

from scripts.release.edge_lockdown_contract import (
    APPLICATION_PROBES,
    BYPASS_HEADER_NAME,
    EDGE_ROUTE_GROUPS,
    ApplicationProbe,
)
from scripts.release.vercel_firewall import EdgeLockdownError
from scripts.release.verify_safe_baseline import (
    _snapshot_database,
    compare_no_side_effect_snapshot,
)


def response_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return str(payload.get("code") or "")


def poll_probe(
    client: httpx.Client,
    probe: ApplicationProbe,
    *,
    extra_headers: dict[str, str] | None,
    expected_status: int,
    expected_code: str | None,
    alternate_outcomes: tuple[tuple[int, str | None], ...] = (),
) -> httpx.Response:
    last: httpx.Response | None = None
    expected_outcomes = ((expected_status, expected_code), *alternate_outcomes)
    headers = {"User-Agent": "vowpic-edge-lockdown/1", **(probe.headers or {})}
    headers.update(extra_headers or {})
    for attempt in range(6):
        last = client.request(
            probe.method,
            probe.path,
            headers=headers,
            json=probe.json_body,
        )
        code = response_code(last)
        if any(
            last.status_code == status and (outcome_code is None or code == outcome_code)
            for status, outcome_code in expected_outcomes
        ):
            return last
        if attempt < 5:
            time.sleep(2)
    assert last is not None
    expected = " or ".join(
        f"{status}/{outcome_code or '*'}" for status, outcome_code in expected_outcomes
    )
    raise EdgeLockdownError(
        f"{probe.method} {probe.path} expected {expected}, "
        f"observed {last.status_code}/{response_code(last) or 'missing'}"
    )


def verify_lockdown_http(base_url: str, bypass_value: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0, follow_redirects=False) as client:
        for group in sorted(EDGE_ROUTE_GROUPS):
            response = poll_probe(
                client,
                APPLICATION_PROBES[group],
                extra_headers=None,
                expected_status=403,
                expected_code=None,
            )
            results[group] = {"status": response.status_code}
        bypass_response = poll_probe(
            client,
            APPLICATION_PROBES["auth_upload"],
            extra_headers={BYPASS_HEADER_NAME: bypass_value},
            expected_status=APPLICATION_PROBES["auth_upload"].expected_status,
            expected_code=APPLICATION_PROBES["auth_upload"].expected_code,
            alternate_outcomes=((503, "runtime_not_ready"),),
        )
        preserved = {}
        preserved_requests = (
            ("health", "GET", "/health", None, {}),
            ("readiness", "GET", "/api/v1/ops/readiness", None, {}),
            (
                "webhook",
                "POST",
                "/api/v1/payments/webhook/creem",
                {"id": "invalid-safe-baseline-event"},
                {"creem-signature": "invalid-safe-baseline-signature"},
            ),
            ("logout", "POST", "/api/v1/auth/logout", None, {}),
        )
        for name, method, path, body, headers in preserved_requests:
            response = client.request(method, path, json=body, headers=headers)
            if response.status_code == 403:
                raise EdgeLockdownError(f"preserved edge path was denied: {name}")
            preserved[name] = response.status_code
    return {
        "groups": results,
        "runner_bypass_status": bypass_response.status_code,
        "runner_bypass_code": response_code(bypass_response),
        "preserved_paths": preserved,
    }


def verify_application_group(
    base_url: str,
    group: str,
    *,
    database_url: str,
    expected_schema: str,
    source_sha: str,
    runtime_bundle_id: str,
) -> dict[str, Any]:
    before = _snapshot_database(
        database_url,
        expected_schema=expected_schema,
        expected_source_sha=source_sha,
        expected_runtime_bundle_id=runtime_bundle_id,
    )
    probe = APPLICATION_PROBES[group]
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0, follow_redirects=False) as client:
        response = poll_probe(
            client,
            probe,
            extra_headers=None,
            expected_status=probe.expected_status,
            expected_code=probe.expected_code,
        )
    after = _snapshot_database(
        database_url,
        expected_schema=expected_schema,
        expected_source_sha=source_sha,
        expected_runtime_bundle_id=runtime_bundle_id,
    )
    comparison = compare_no_side_effect_snapshot(before, after)
    if not comparison["matches"]:
        raise EdgeLockdownError(f"application probe for {group} changed protected database state")
    return {
        "application_status": response.status_code,
        "application_code": response_code(response),
        "no_side_effects": True,
        "before_snapshot_sha256": before["snapshot_sha256"],
        "after_snapshot_sha256": after["snapshot_sha256"],
    }
