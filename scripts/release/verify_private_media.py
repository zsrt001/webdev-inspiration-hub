#!/usr/bin/env python3
"""Reconcile private-copy/delete reports without exposing legacy references."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_RejectRedirects)


def _load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _exact_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("API base URL must be an exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _probe_json(origin: str, path: str) -> dict[str, Any]:
    try:
        with _NO_REDIRECT_OPENER.open(
            Request(f"{origin}{path}", headers={"Accept": "application/json"}),
            timeout=20,
        ) as response:
            if response.status != 200:
                raise ValueError(f"{path} returned HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} probe failed") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not return a JSON object")
    return payload


def build_private_media_verdict(
    *,
    inventory: dict[str, Any],
    migration_report: dict[str, Any],
    probe_reports: list[dict[str, Any]] | None = None,
    live_probe: dict[str, Any] | None = None,
    now: datetime | None = None,
    maximum_probe_age_seconds: int = 900,
) -> dict[str, Any]:
    if inventory.get("schema") != "vowpic.production-inventory.v2":
        raise ValueError("inventory schema is unsupported")
    if (
        migration_report.get("tool") != "migrate_public_media"
        or migration_report.get("passed") is not True
    ):
        raise ValueError("media migration report did not PASS")
    counts = migration_report.get("counts")
    blockers = migration_report.get("blockers")
    if not isinstance(counts, dict) or not isinstance(blockers, dict) or blockers:
        raise ValueError("media migration report is incomplete or blocking")
    mode = str(migration_report.get("mode") or "")
    eligible = int(counts.get("eligible") or 0)
    if mode.startswith("copy-"):
        reconciled = int(counts.get("copied") or 0) + int(
            counts.get("already_copied") or 0
        )
    elif mode.startswith("delete-"):
        reconciled = int(counts.get("deleted") or 0) + int(
            counts.get("already_deleted") or 0
        )
    else:
        raise ValueError("media migration mode is unsupported")
    if reconciled != eligible:
        raise ValueError("media migration counts do not reconcile")
    probe_locations: list[str] = []
    probe_hmac_sets: list[set[str]] = []
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for report in probe_reports or []:
        if (
            report.get("schema") != "vowpic.legacy-url-invalidation.v1"
            or report.get("passed") is not True
            or int(report.get("failed_count") or 0) != 0
            or int(report.get("probed_count") or 0)
            != int(report.get("expected_count") or -1)
            or report.get("inventory_sha256")
            != migration_report.get("inventory_sha256")
            or report.get("manifest_sha256")
            != migration_report.get("manifest_sha256")
        ):
            raise ValueError("legacy URL invalidation report is incomplete")
        location_id = str(report.get("location_id") or "").strip()
        if not location_id or location_id in probe_locations:
            raise ValueError("legacy URL probe locations must be distinct")
        results = report.get("results")
        if not isinstance(results, list):
            raise ValueError("legacy URL invalidation results are missing")
        try:
            verified_at = datetime.fromisoformat(
                str(report.get("verified_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "legacy URL invalidation timestamp is invalid"
            ) from exc
        if verified_at.tzinfo is None:
            raise ValueError("legacy URL invalidation timestamp lacks timezone")
        verified_at = verified_at.astimezone(timezone.utc)
        if (
            verified_at > current.replace(microsecond=0)
            + timedelta(seconds=60)
            or (current - verified_at).total_seconds()
            > max(1, int(maximum_probe_age_seconds))
        ):
            raise ValueError("legacy URL invalidation report is stale")
        hmacs = {
            str(result.get("url_hmac_sha256") or "")
            for result in results
            if isinstance(result, dict)
        }
        if (
            len(hmacs) != len(results)
            or len(hmacs) != int(report.get("expected_count") or -1)
            or any(
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in hmacs
            )
        ):
            raise ValueError("legacy URL invalidation HMAC set is incomplete")
        requests_per_url = int(report.get("requests_per_url") or 0)
        for result in results:
            attempts = result.get("attempts")
            if (
                result.get("passed") is not True
                or not isinstance(attempts, list)
                or len(attempts) != requests_per_url
                or requests_per_url < 1
                or any(
                    not isinstance(attempt, dict)
                    or attempt.get("passed") is not True
                    or int(attempt.get("status") or 0) not in {404, 410}
                    or attempt.get("differs_from_old_checksum") is not True
                    for attempt in attempts
                )
            ):
                raise ValueError(
                    "legacy URL invalidation attempts are incomplete"
                )
        probe_locations.append(location_id)
        probe_hmac_sets.append(hmacs)
    if mode.startswith("delete-") and len(probe_locations) != 2:
        raise ValueError(
            "delete verification requires exactly two independent probe locations"
        )
    if probe_hmac_sets and any(
        item != probe_hmac_sets[0]
        for item in probe_hmac_sets[1:]
    ):
        raise ValueError("independent legacy URL probe sets do not match")
    if live_probe is not None:
        if live_probe.get("ready") is not True or not live_probe.get("deployment_id"):
            raise ValueError("private-compatible API is not ready")
        if (
            live_probe.get("runtime_bundle_id")
            != migration_report.get("runtime_bundle_id")
        ):
            raise ValueError("private-compatible API runtime bundle drift")
    return {
        "schema": "vowpic.private-media-verification.v1",
        "passed": True,
        "migration_mode": mode,
        "eligible_count": eligible,
        "reconciled_count": reconciled,
        "inventory_url_set_sha256": inventory["url_inventory_hmac_sha256"],
        "probe_locations": sorted(probe_locations),
        "live_private_compatible_probe": live_probe is not None,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--migration-report", required=True)
    parser.add_argument("--probe-report", action="append", default=[])
    parser.add_argument("--api-base-url")
    parser.add_argument("--require-private-read-all", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        inventory, inventory_sha = _load(Path(args.inventory))
        migration, migration_sha = _load(Path(args.migration_report))
        probes = [_load(Path(path))[0] for path in args.probe_report]
        live_probe = None
        if args.api_base_url:
            origin = _exact_origin(args.api_base_url)
            ready = _probe_json(origin, "/health/ready")
            version = _probe_json(origin, "/version")
            live_probe = {
                "ready": ready.get("status") == "ready",
                "deployment_id": version.get("deployment_id"),
                "runtime_bundle_id": version.get("runtime_bundle_id"),
            }
        if args.require_private_read_all and live_probe is None:
            raise ValueError("--require-private-read-all requires a live API probe")
        verdict = build_private_media_verdict(
            inventory=inventory,
            migration_report=migration,
            probe_reports=probes,
            live_probe=live_probe,
        )
        verdict["inventory_sha256"] = inventory_sha
        verdict["migration_report_sha256"] = migration_sha
        _write_create_once(Path(args.output), verdict)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
