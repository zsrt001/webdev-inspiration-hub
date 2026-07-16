#!/usr/bin/env python3
"""Build and control one digest-pinned ephemeral Preview Worker."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}@sha256:[0-9a-f]{64}$")
_CONTAINER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_RUNTIME_BUNDLE = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _source_sha(value: str) -> str:
    clean = str(value or "").strip().lower()
    if not _SOURCE_SHA.fullmatch(clean):
        raise ValueError("Preview Worker source SHA is invalid")
    return clean


def build_command(*, source_sha: str, oci_output: Path) -> list[str]:
    source = _source_sha(source_sha)
    if oci_output.exists():
        raise FileExistsError("Preview Worker OCI output already exists")
    return [
        "docker",
        "buildx",
        "build",
        "--file",
        "backend/Dockerfile.worker",
        "--platform",
        "linux/amd64",
        "--provenance=true",
        "--sbom=true",
        "--tag",
        f"vowpic-preview-worker:{source}",
        "--output",
        f"type=oci,dest={oci_output.as_posix()}",
        ".",
    ]


def create_command(*, image_reference: str, container_name: str, env_file: Path) -> list[str]:
    image = str(image_reference or "").strip().lower()
    name = str(container_name or "").strip()
    if not _IMAGE_REFERENCE.fullmatch(image):
        raise ValueError("Preview Worker image reference must be digest-pinned")
    if not _CONTAINER.fullmatch(name):
        raise ValueError("Preview Worker container name is invalid")
    return [
        "docker",
        "create",
        "--name",
        name,
        "--env-file",
        str(env_file),
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=256m",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory=2g",
        "--cpus=2",
        "--pids-limit=256",
        "--restart=no",
        image,
    ]


def build_process_report(
    *,
    source_sha: str,
    runtime_bundle_id: str,
    api_deployment_id: str,
    image_reference: str,
    container_id: str,
    container_name: str,
    state: str = "RUNNING",
    now: datetime | None = None,
) -> dict[str, Any]:
    source = _source_sha(source_sha)
    runtime = str(runtime_bundle_id or "").strip().lower()
    deployment = str(api_deployment_id or "").strip()
    image = str(image_reference or "").strip().lower()
    worker_id = str(container_id or "").strip().lower()
    name = str(container_name or "").strip()
    if not _RUNTIME_BUNDLE.fullmatch(runtime):
        raise ValueError("Preview Worker runtime bundle ID is invalid")
    if not _DEPLOYMENT.fullmatch(deployment):
        raise ValueError("Preview Worker API deployment ID is invalid")
    if not _IMAGE_REFERENCE.fullmatch(image):
        raise ValueError("Preview Worker image reference must be digest-pinned")
    if not re.fullmatch(r"[0-9a-f]{12,64}", worker_id):
        raise ValueError("Preview Worker container ID is invalid")
    if not _CONTAINER.fullmatch(name):
        raise ValueError("Preview Worker container name is invalid")
    if state not in {"CREATED", "RUNNING"}:
        raise ValueError("Preview Worker process state is invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Preview Worker start timestamp must be timezone-aware")
    return {
        "schema": "vowpic.preview-worker-process.v1",
        "state": state,
        "source_sha": source,
        "runtime_bundle_id": runtime,
        "api_deployment_id": deployment,
        "worker_image_digest": image.rsplit("@", 1)[1],
        "container_id": worker_id,
        "container_name": name,
        "observed_at": current.astimezone(timezone.utc).isoformat(),
    }


def validate_runtime_heartbeat_payload(
    payload: dict[str, Any],
    *,
    source_sha: str,
    runtime_bundle_id: str,
    api_deployment_id: str,
    worker_deployment_id: str,
    worker_image_digest: str,
    current_snapshot_hash: str,
    target_snapshot_hash: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    required = {
        "schema", "worker_id", "environment", "source_sha", "runtime_bundle_id",
        "api_deployment_id", "worker_deployment_id", "worker_image_digest",
        "schema_revision", "payload_min", "payload_max", "config_hash",
        "current_feature_snapshot_hash", "target_feature_snapshot_hash", "published_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Preview Worker heartbeat schema mismatch")
    expected = {
        "schema": "vowpic.worker-heartbeat.v1",
        "environment": "preview",
        "source_sha": _source_sha(source_sha),
        "runtime_bundle_id": str(runtime_bundle_id or "").strip().lower(),
        "api_deployment_id": str(api_deployment_id or "").strip(),
        "worker_deployment_id": str(worker_deployment_id or "").strip().lower(),
        "worker_image_digest": str(worker_image_digest or "").strip().lower(),
        "schema_revision": "20260710_0020",
        "payload_min": "generation-job.v1",
        "payload_max": "generation-job.v1",
        "current_feature_snapshot_hash": str(current_snapshot_hash or "").strip().lower(),
        "target_feature_snapshot_hash": str(target_snapshot_hash or "").strip().lower(),
    }
    if not _RUNTIME_BUNDLE.fullmatch(expected["runtime_bundle_id"]):
        raise ValueError("Preview Worker expected runtime bundle ID is invalid")
    if not _DEPLOYMENT.fullmatch(expected["api_deployment_id"]):
        raise ValueError("Preview Worker expected API deployment ID is invalid")
    if not re.fullmatch(r"[0-9a-f]{12,64}", expected["worker_deployment_id"]):
        raise ValueError("Preview Worker expected deployment ID is invalid")
    if not _DIGEST.fullmatch(expected["worker_image_digest"]):
        raise ValueError("Preview Worker expected image digest is invalid")
    for field in ("current_feature_snapshot_hash", "target_feature_snapshot_hash"):
        if not _HEX64.fullmatch(expected[field]):
            raise ValueError(f"Preview Worker expected {field} is invalid")
    if any(payload.get(field) != value for field, value in expected.items()):
        raise ValueError("Preview Worker heartbeat coordinate mismatch")
    if not str(payload.get("worker_id") or "").strip() or len(str(payload["worker_id"])) > 128:
        raise ValueError("Preview Worker heartbeat worker ID is invalid")
    if not _HEX64.fullmatch(str(payload.get("config_hash") or "")):
        raise ValueError("Preview Worker heartbeat config hash is invalid")
    try:
        published_at = datetime.fromisoformat(str(payload.get("published_at") or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Preview Worker heartbeat timestamp is invalid") from exc
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("Preview Worker heartbeat timestamp must be timezone-aware")
    current = now or datetime.now(timezone.utc)
    age = current.astimezone(timezone.utc) - published_at.astimezone(timezone.utc)
    if age < -timedelta(seconds=5) or age > timedelta(seconds=120):
        raise ValueError("Preview Worker heartbeat is stale or from the future")
    return dict(payload)


def validate_cleanup_heartbeat_payload(
    payload: dict[str, Any] | None,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    api_deployment_id: str,
    worker_deployment_id: str,
    worker_image_digest: str,
    current_snapshot_hash: str,
    target_snapshot_hash: str,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    try:
        published_at = datetime.fromisoformat(
            str(payload.get("published_at") or "").replace("Z", "+00:00")
        )
    except (AttributeError, ValueError) as exc:
        raise ValueError("Preview Worker cleanup heartbeat timestamp is invalid") from exc
    return validate_runtime_heartbeat_payload(
        payload,
        source_sha=source_sha,
        runtime_bundle_id=runtime_bundle_id,
        api_deployment_id=api_deployment_id,
        worker_deployment_id=worker_deployment_id,
        worker_image_digest=worker_image_digest,
        current_snapshot_hash=current_snapshot_hash,
        target_snapshot_hash=target_snapshot_hash,
        now=published_at,
    )


async def _read_runtime_heartbeat(redis_url: str, runtime_bundle_id: str) -> dict[str, Any]:
    from redis.asyncio import from_url

    if not redis_url.strip():
        raise ValueError("Preview Redis URL is required")
    key = f"vowpic:worker-heartbeat:v1:preview:{runtime_bundle_id}"
    client = from_url(redis_url, decode_responses=True)
    try:
        raw = await client.get(key)
    finally:
        await client.aclose()
    if raw is None:
        raise ValueError("Preview Worker runtime heartbeat is missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Preview Worker runtime heartbeat is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Preview Worker runtime heartbeat is not an object")
    return payload


async def _remove_runtime_heartbeat(
    redis_url: str,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    api_deployment_id: str,
    worker_deployment_id: str,
    worker_image_digest: str,
    current_snapshot_hash: str,
    target_snapshot_hash: str,
) -> str:
    from redis.asyncio import from_url

    runtime = str(runtime_bundle_id or "").strip().lower()
    if not redis_url.strip() or not _RUNTIME_BUNDLE.fullmatch(runtime):
        raise ValueError("Preview Redis URL or runtime bundle ID is invalid")
    key = f"vowpic:worker-heartbeat:v1:preview:{runtime}"
    client = from_url(redis_url, decode_responses=True)
    try:
        raw = await client.get(key)
        if raw is None:
            return "ALREADY_ABSENT"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Preview Worker cleanup heartbeat is invalid JSON") from exc
        validate_cleanup_heartbeat_payload(
            payload,
            source_sha=source_sha,
            runtime_bundle_id=runtime,
            api_deployment_id=api_deployment_id,
            worker_deployment_id=worker_deployment_id,
            worker_image_digest=worker_image_digest,
            current_snapshot_hash=current_snapshot_hash,
            target_snapshot_hash=target_snapshot_hash,
        )
        removed = await client.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end",
            1,
            key,
            raw,
        )
        if removed != 1:
            raise ValueError("Preview Worker heartbeat changed during cleanup")
        if await client.get(key) is not None:
            raise ValueError("Preview Worker heartbeat still exists after cleanup")
        return "REMOVED"
    finally:
        await client.aclose()


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="strict",
    )


def _oci_manifest_digest(path: Path) -> str:
    if not path.is_file():
        raise ValueError("Preview Worker OCI archive is missing")
    with tarfile.open(path, mode="r:*") as archive:
        member = archive.getmember("index.json")
        index = json.loads(archive.extractfile(member).read().decode("utf-8"))  # type: ignore[union-attr]
        manifests = index.get("manifests") if isinstance(index, dict) else None
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("Preview Worker OCI archive must contain one manifest")
        digest = str(manifests[0].get("digest") or "").lower()
        if not _DIGEST.fullmatch(digest):
            raise ValueError("Preview Worker OCI manifest digest is invalid")
        blob = archive.extractfile(f"blobs/sha256/{digest.split(':', 1)[1]}")
        if blob is None or "sha256:" + hashlib.sha256(blob.read()).hexdigest() != digest:
            raise ValueError("Preview Worker OCI manifest digest read-back failed")
        return digest


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _write_job_env(path: Path | None, prefix: str, values: dict[str, str]) -> None:
    if path is None:
        return
    clean_prefix = str(prefix or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}_", clean_prefix):
        raise ValueError("Preview Worker job-env prefix is invalid")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in sorted(values.items()):
            if "\n" in value or "\r" in value:
                raise ValueError("Preview Worker job-env value is invalid")
            handle.write(f"{clean_prefix}{key}={value}\n")


def build_worker(
    *,
    source_sha: str,
    oci_output: Path,
    report_path: Path,
    job_env: Path | None,
    env_prefix: str,
) -> dict[str, Any]:
    source = _source_sha(source_sha)
    head = _run(["git", "rev-parse", "HEAD"], capture=True).stdout.strip().lower()
    if head != source:
        raise ValueError("Preview Worker build source does not match the checkout")
    _run(build_command(source_sha=source, oci_output=oci_output))
    digest = _oci_manifest_digest(oci_output)
    archive_sha = hashlib.sha256(oci_output.read_bytes()).hexdigest()
    image_reference = f"vowpic-preview-worker@{digest}"
    report = {
        "schema": "vowpic.preview-worker-build.v1",
        "passed": True,
        "source_sha": source,
        "worker_image_digest": digest,
        "image_reference": image_reference,
        "oci_archive_sha256": archive_sha,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": "docker-buildx-oci",
    }
    _write_create_once(report_path, report)
    _write_job_env(
        job_env,
        env_prefix,
        {
            "IMAGE_DIGEST": digest,
            "IMAGE_REFERENCE": image_reference,
            "OCI_ARCHIVE_SHA256": archive_sha,
        },
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--source-sha", required=True)
    build.add_argument("--oci-output", required=True)
    build.add_argument("--report", required=True)
    build.add_argument("--job-env")
    build.add_argument("--env-prefix", default="PREVIEW_WORKER_")
    load = subparsers.add_parser("load")
    load.add_argument("--oci-input", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--image-reference", required=True)
    create.add_argument("--container-name", required=True)
    create.add_argument("--env-file", required=True)
    create.add_argument("--source-sha", required=True)
    create.add_argument("--runtime-bundle-id", required=True)
    create.add_argument("--api-deployment-id", required=True)
    create.add_argument("--report", required=True)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--container-name", required=True)
    resume.add_argument("--report", required=True)
    heartbeat = subparsers.add_parser("heartbeat")
    heartbeat.add_argument("--container-name", required=True)
    heartbeat.add_argument("--source-sha", required=True)
    heartbeat.add_argument("--runtime-bundle-id", required=True)
    heartbeat.add_argument("--api-deployment-id", required=True)
    heartbeat.add_argument("--worker-deployment-id", required=True)
    heartbeat.add_argument("--worker-image-digest", required=True)
    heartbeat.add_argument("--current-snapshot-hash", required=True)
    heartbeat.add_argument("--target-snapshot-hash", required=True)
    heartbeat.add_argument("--redis-url-env", default="PREVIEW_REDIS_URL")
    heartbeat.add_argument("--report", required=True)
    stop = subparsers.add_parser("stop")
    stop.add_argument("--container-name", required=True)
    stop.add_argument("--source-sha", required=True)
    stop.add_argument("--runtime-bundle-id", required=True)
    stop.add_argument("--api-deployment-id", required=True)
    stop.add_argument("--worker-deployment-id", required=True)
    stop.add_argument("--worker-image-digest", required=True)
    stop.add_argument("--current-snapshot-hash", required=True)
    stop.add_argument("--target-snapshot-hash", required=True)
    stop.add_argument("--redis-url-env", default="PREVIEW_REDIS_URL")
    stop.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            result = build_worker(
                source_sha=args.source_sha,
                oci_output=Path(args.oci_output),
                report_path=Path(args.report),
                job_env=Path(args.job_env) if args.job_env else None,
                env_prefix=args.env_prefix,
            )
        elif args.action == "load":
            _run(["docker", "load", "--input", str(Path(args.oci_input))])
            result = {"state": "LOADED"}
        elif args.action == "create":
            command = create_command(
                image_reference=args.image_reference,
                container_name=args.container_name,
                env_file=Path(args.env_file),
            )
            completed = _run(
                command,
                capture=True,
            )
            container_id = completed.stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                raise ValueError("Preview Worker container ID is invalid")
            result = build_process_report(
                source_sha=args.source_sha,
                runtime_bundle_id=args.runtime_bundle_id,
                api_deployment_id=args.api_deployment_id,
                image_reference=args.image_reference,
                container_id=container_id,
                container_name=args.container_name,
                state="CREATED",
            )
            _write_create_once(Path(args.report), result)
        elif args.action == "resume":
            _run(["docker", "start", args.container_name], capture=True)
            completed = _run(
                ["docker", "inspect", "--format", "{{.State.Running}}", args.container_name],
                capture=True,
            )
            if completed.stdout.strip().lower() != "true":
                raise ValueError("Preview Worker container did not start")
            result = {
                "schema": "vowpic.preview-worker-resume.v1",
                "state": "RUNNING",
                "container_name": args.container_name,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_create_once(Path(args.report), result)
        elif args.action == "heartbeat":
            completed = _run(
                ["docker", "inspect", "--format", "{{.State.Running}}", args.container_name],
                capture=True,
            )
            if completed.stdout.strip().lower() != "true":
                raise ValueError("Preview Worker container is not running")
            heartbeat_payload = asyncio.run(
                _read_runtime_heartbeat(
                    os.environ.get(args.redis_url_env, ""),
                    str(args.runtime_bundle_id).strip().lower(),
                )
            )
            validated = validate_runtime_heartbeat_payload(
                heartbeat_payload,
                source_sha=args.source_sha,
                runtime_bundle_id=args.runtime_bundle_id,
                api_deployment_id=args.api_deployment_id,
                worker_deployment_id=args.worker_deployment_id,
                worker_image_digest=args.worker_image_digest,
                current_snapshot_hash=args.current_snapshot_hash,
                target_snapshot_hash=args.target_snapshot_hash,
            )
            result = {
                "schema": "vowpic.preview-worker-heartbeat-report.v1",
                "state": "RUNNING",
                "container_name": args.container_name,
                "heartbeat": validated,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_create_once(Path(args.report), result)
        else:
            api_deployment_id = str(args.api_deployment_id or "").strip()
            worker_image_digest = str(args.worker_image_digest or "").strip().lower()
            if not _DEPLOYMENT.fullmatch(api_deployment_id):
                raise ValueError("Preview Worker cleanup API deployment ID is invalid")
            if not _DIGEST.fullmatch(worker_image_digest):
                raise ValueError("Preview Worker cleanup image digest is invalid")
            _run(["docker", "info"], capture=True)
            existing = subprocess.run(
                ["docker", "inspect", args.container_name],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if existing.returncode == 0:
                _run(["docker", "rm", "--force", args.container_name])
            probe = subprocess.run(
                ["docker", "inspect", args.container_name],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if probe.returncode == 0:
                raise ValueError("Preview Worker container still exists after cleanup")
            heartbeat_cleanup = asyncio.run(
                _remove_runtime_heartbeat(
                    os.environ.get(args.redis_url_env, ""),
                    source_sha=args.source_sha,
                    runtime_bundle_id=args.runtime_bundle_id,
                    api_deployment_id=args.api_deployment_id,
                    worker_deployment_id=args.worker_deployment_id,
                    worker_image_digest=args.worker_image_digest,
                    current_snapshot_hash=args.current_snapshot_hash,
                    target_snapshot_hash=args.target_snapshot_hash,
                )
            )
            result = {
                "schema": "vowpic.preview-worker-process.v1",
                "state": "STOPPED",
                "container_name": args.container_name,
                "source_sha": _source_sha(args.source_sha),
                "runtime_bundle_id": str(args.runtime_bundle_id).strip().lower(),
                "api_deployment_id": api_deployment_id,
                "worker_deployment_id": str(args.worker_deployment_id).strip().lower(),
                "worker_image_digest": worker_image_digest,
                "heartbeat_state": "ABSENT",
                "heartbeat_cleanup": heartbeat_cleanup,
                "stopped_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_create_once(Path(args.report), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
