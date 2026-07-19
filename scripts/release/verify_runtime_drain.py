#!/usr/bin/env python3
"""Prove old API/Worker coordinates and the legacy generation queue are drained."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_COORDINATE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_RejectRedirects)


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
        raise ValueError("formal base URL must be an exact HTTPS origin")
    return f"https://{parsed.netloc}"


def _get_json(origin: str, path: str) -> dict[str, Any]:
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


def _load_old_coordinates(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [line.strip() for line in raw.splitlines() if line.strip()]
    values: list[object]
    if isinstance(parsed, list):
        values = parsed
    elif isinstance(parsed, dict):
        values = []
        for value in parsed.values():
            values.extend(value if isinstance(value, list) else [value])
    else:
        raise ValueError("old runtime coordinate file is invalid")
    coordinates: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            nested = [
                value.get("api_deployment_id"),
                value.get("worker_deployment_id"),
            ]
        else:
            nested = [value]
        for coordinate in nested:
            clean = str(coordinate or "").strip()
            if not clean:
                continue
            if not _COORDINATE.fullmatch(clean):
                raise ValueError("old runtime coordinate is invalid")
            coordinates.add(clean)
    return coordinates


async def _legacy_queue_depth(redis_url: str, queue_name: str) -> int:
    if not redis_url:
        raise ValueError("REDIS_URL is required to prove legacy queue drain")
    from redis.asyncio import from_url

    client = from_url(redis_url, decode_responses=False)
    try:
        keys = (
            f"arq:queue:{queue_name}",
            f"arq:queue:{queue_name}:in-progress",
            f"vowpic:legacy-queue:{queue_name}",
        )
        total = 0
        for key in keys:
            key_type = await client.type(key)
            if key_type in {b"none", "none"}:
                continue
            if key_type in {b"zset", "zset"}:
                total += int(await client.zcard(key))
            elif key_type in {b"list", "list"}:
                total += int(await client.llen(key))
            else:
                raise ValueError("legacy queue key has an unexpected Redis type")
        return total
    finally:
        await client.aclose()


def build_drain_verdict(
    *,
    version: dict[str, Any],
    ready: dict[str, Any],
    expected_api_deployment_id: str,
    expected_worker_deployment_id: str,
    old_coordinates: set[str],
    queue_depth: int,
    worker_heartbeat: dict[str, Any] | None,
    waited_seconds: int,
) -> dict[str, Any]:
    actual_api = str(version.get("deployment_id") or "").strip()
    if actual_api != expected_api_deployment_id:
        raise ValueError("formal API deployment ID mismatch")
    if actual_api in old_coordinates:
        raise ValueError("formal API still serves an old runtime")
    if ready.get("status") != "ready":
        raise ValueError("formal API is not ready")
    expected_worker = str(expected_worker_deployment_id or "").strip()
    if expected_worker == "none":
        if worker_heartbeat is not None:
            raise ValueError("Worker heartbeat must be absent")
    else:
        if worker_heartbeat is None:
            raise ValueError("expected Worker heartbeat report is missing")
        observed = str(
            (worker_heartbeat.get("heartbeat") or {}).get("worker_deployment_id")
            or worker_heartbeat.get("worker_deployment_id")
            or ""
        )
        if observed != expected_worker or observed in old_coordinates:
            raise ValueError("Worker deployment drain coordinate mismatch")
    if queue_depth != 0:
        raise ValueError("legacy generation queue is not empty")
    return {
        "schema": "vowpic.runtime-drain.v1",
        "passed": True,
        "api_deployment_id": actual_api,
        "worker_deployment_id": expected_worker,
        "legacy_queue_depth": queue_depth,
        "old_coordinate_count": len(old_coordinates),
        "waited_seconds": waited_seconds,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


async def _run(args: argparse.Namespace) -> None:
    origin = _exact_origin(args.formal_base_url)
    if not _COORDINATE.fullmatch(args.expected_api_deployment_id):
        raise ValueError("expected API deployment ID is invalid")
    if args.expected_worker_deployment_id != "none" and not _COORDINATE.fullmatch(
        args.expected_worker_deployment_id
    ):
        raise ValueError("expected Worker deployment ID is invalid")
    old_path_value = os.environ.get(args.old_deployments_file_env, "").strip()
    if not old_path_value:
        raise ValueError("old runtime coordinate file is required")
    old_coordinates = _load_old_coordinates(Path(old_path_value))
    wait_seconds = max(0, int(args.max_api_duration_seconds)) + int(
        args.margin_seconds
    )
    if wait_seconds:
        await asyncio.sleep(wait_seconds)
    version = await asyncio.to_thread(_get_json, origin, "/version")
    ready = await asyncio.to_thread(_get_json, origin, "/health/ready")
    queue_depth = await _legacy_queue_depth(
        os.environ.get(args.redis_url_env, "").strip(),
        args.legacy_queue_name,
    )
    heartbeat = None
    heartbeat_path = os.environ.get(args.worker_heartbeat_report_env, "").strip()
    if heartbeat_path:
        heartbeat = json.loads(Path(heartbeat_path).read_text(encoding="utf-8"))
    verdict = build_drain_verdict(
        version=version,
        ready=ready,
        expected_api_deployment_id=args.expected_api_deployment_id,
        expected_worker_deployment_id=args.expected_worker_deployment_id,
        old_coordinates=old_coordinates,
        queue_depth=queue_depth,
        worker_heartbeat=heartbeat,
        waited_seconds=wait_seconds,
    )
    _write_create_once(Path(args.output), verdict)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-base-url", required=True)
    parser.add_argument("--expected-api-deployment-id", required=True)
    parser.add_argument("--expected-worker-deployment-id", required=True)
    parser.add_argument("--old-deployments-file-env", required=True)
    parser.add_argument("--legacy-queue-name", required=True)
    parser.add_argument("--redis-url-env", default="REDIS_URL")
    parser.add_argument(
        "--worker-heartbeat-report-env",
        default="WORKER_HEARTBEAT_REPORT",
    )
    parser.add_argument("--max-api-duration-seconds", type=int, required=True)
    parser.add_argument("--margin-seconds", type=int, default=30)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
