#!/usr/bin/env python3
"""Resolve immutable GitHub Actions artifacts without treating API errors as absence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any
import zipfile

import httpx


API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
REFERENCE_PREFIX = "gha:v1:"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024


class GitHubArtifactError(RuntimeError):
    pass


def _repository(value: str) -> tuple[str, str]:
    clean = str(value or "").strip()
    parts = clean.split("/")
    if len(parts) != 2 or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts
    ):
        raise ValueError("repository must be an owner/name pair")
    return parts[0], parts[1]


def _positive_id(value: str | int, *, label: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", clean):
        raise ValueError(f"{label} must be a positive decimal ID")
    return clean


def _artifact_digest(value: str) -> str:
    clean = str(value or "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", clean):
        raise ValueError("artifact digest must be sha256:<64 lowercase hex>")
    return clean


def _report_name(value: str) -> str:
    clean = str(value or "").strip()
    path = PurePosixPath(clean)
    if (
        not clean
        or path.is_absolute()
        or len(path.parts) != 1
        or clean in {".", ".."}
        or not clean.endswith(".json")
    ):
        raise ValueError("report name must be one JSON basename")
    return clean


def _headers(token: str) -> dict[str, str]:
    clean = str(token or "").strip()
    if not clean:
        raise ValueError("GitHub token is required")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {clean}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "vowpic-artifact-evidence/1",
    }


def lookup_artifact(
    *,
    repository: str,
    run_id: str | int,
    name: str,
    token: str,
    client: httpx.Client,
) -> dict[str, str]:
    """Return FOUND/NOT_FOUND only after one successful authoritative API read."""
    owner, repo = _repository(repository)
    expected_run = _positive_id(run_id, label="workflow run ID")
    expected_name = str(name or "").strip()
    if not expected_name or len(expected_name) > 255:
        raise ValueError("artifact name is required")
    response = client.get(
        f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts",
        headers=_headers(token),
        params={"name": expected_name, "per_page": 100},
    )
    if response.status_code != 200:
        raise GitHubArtifactError(
            f"GitHub artifact lookup failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubArtifactError("GitHub artifact lookup returned invalid JSON") from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        raise GitHubArtifactError("GitHub artifact lookup omitted its artifact list")
    matches = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("name") != expected_name:
            continue
        workflow_run = artifact.get("workflow_run")
        if not isinstance(workflow_run, dict) or str(workflow_run.get("id")) != expected_run:
            continue
        if bool(artifact.get("expired")):
            continue
        matches.append(artifact)
    if not matches:
        return {"state": "NOT_FOUND"}
    if len(matches) != 1:
        raise GitHubArtifactError("GitHub artifact lookup is ambiguous")
    artifact = matches[0]
    return {
        "state": "FOUND",
        "artifact_id": _positive_id(artifact.get("id"), label="artifact ID"),
        "artifact_digest": _artifact_digest(str(artifact.get("digest") or "")),
    }


def build_reference(
    *,
    repository: str,
    run_id: str | int,
    artifact_id: str | int,
    artifact_digest: str,
    report_name: str,
) -> str:
    owner, repo = _repository(repository)
    payload = {
        "artifact_digest": _artifact_digest(artifact_digest),
        "artifact_id": _positive_id(artifact_id, label="artifact ID"),
        "report_name": _report_name(report_name),
        "repository": f"{owner}/{repo}",
        "run_id": _positive_id(run_id, label="workflow run ID"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    reference = f"{REFERENCE_PREFIX}{encoded}"
    if len(reference) > 512:
        raise ValueError("artifact evidence reference exceeds the database limit")
    return reference


def parse_reference(reference: str) -> dict[str, str]:
    clean = str(reference or "").strip()
    if not clean.startswith(REFERENCE_PREFIX):
        raise ValueError("not a GitHub Actions artifact evidence reference")
    token = clean.removeprefix(REFERENCE_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact evidence reference is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "artifact_digest",
        "artifact_id",
        "report_name",
        "repository",
        "run_id",
    }:
        raise ValueError("artifact evidence reference has an invalid schema")
    return {
        "repository": "/".join(_repository(str(payload["repository"]))),
        "run_id": _positive_id(payload["run_id"], label="workflow run ID"),
        "artifact_id": _positive_id(payload["artifact_id"], label="artifact ID"),
        "artifact_digest": _artifact_digest(str(payload["artifact_digest"])),
        "report_name": _report_name(str(payload["report_name"])),
    }


def read_report(
    reference: str,
    *,
    token: str,
    client: httpx.Client,
) -> tuple[bytes, dict[str, Any]]:
    coordinates = parse_reference(reference)
    owner, repo = _repository(coordinates["repository"])
    artifact_id = coordinates["artifact_id"]
    headers = _headers(token)
    metadata = client.get(
        f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}",
        headers=headers,
    )
    if metadata.status_code != 200:
        raise GitHubArtifactError(
            f"GitHub artifact metadata read failed with HTTP {metadata.status_code}"
        )
    try:
        artifact = metadata.json()
    except ValueError as exc:
        raise GitHubArtifactError("GitHub artifact metadata returned invalid JSON") from exc
    workflow_run = artifact.get("workflow_run") if isinstance(artifact, dict) else None
    if (
        not isinstance(artifact, dict)
        or str(artifact.get("id")) != artifact_id
        or bool(artifact.get("expired"))
        or not isinstance(workflow_run, dict)
        or str(workflow_run.get("id")) != coordinates["run_id"]
        or _artifact_digest(str(artifact.get("digest") or ""))
        != coordinates["artifact_digest"]
    ):
        raise GitHubArtifactError("GitHub artifact metadata does not match the stored reference")

    archive = client.get(
        f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip",
        headers=headers,
        follow_redirects=True,
    )
    if archive.status_code != 200:
        raise GitHubArtifactError(
            f"GitHub artifact download failed with HTTP {archive.status_code}"
        )
    archive_raw = archive.content
    if not archive_raw or len(archive_raw) > MAX_ARCHIVE_BYTES:
        raise GitHubArtifactError("GitHub artifact archive is empty or exceeds the size limit")
    actual_digest = f"sha256:{hashlib.sha256(archive_raw).hexdigest()}"
    if actual_digest != coordinates["artifact_digest"]:
        raise GitHubArtifactError("GitHub artifact archive digest mismatch")

    try:
        with zipfile.ZipFile(io.BytesIO(archive_raw)) as zipped:
            matches = [
                info for info in zipped.infolist() if info.filename == coordinates["report_name"]
            ]
            if len(matches) != 1:
                raise GitHubArtifactError("GitHub artifact does not contain one exact report")
            info = matches[0]
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir() or stat.S_ISLNK(mode) or info.file_size > MAX_REPORT_BYTES:
                raise GitHubArtifactError("GitHub artifact report entry is unsafe")
            raw = zipped.read(info)
    except zipfile.BadZipFile as exc:
        raise GitHubArtifactError("GitHub artifact is not a valid ZIP archive") from exc
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubArtifactError("GitHub artifact report is not valid UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise GitHubArtifactError("GitHub artifact report must be a JSON object")
    return raw, report


def verify_reference(
    reference: str,
    *,
    expected_report_sha256: str,
    token: str,
    client: httpx.Client,
) -> dict[str, Any]:
    expected = str(expected_report_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("expected report SHA-256 must be 64 lowercase hex characters")
    raw, report = read_report(reference, token=token, client=client)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise GitHubArtifactError("stored formal report SHA-256 mismatch")
    if report.get("passed") is not True:
        raise GitHubArtifactError("stored formal report is not a PASS report")
    coordinates = parse_reference(reference)
    return {
        "passed": True,
        "artifact_id": coordinates["artifact_id"],
        "artifact_digest": coordinates["artifact_digest"],
        "report_name": coordinates["report_name"],
        "report_sha256": actual,
    }


def _write_job_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in sorted(values.items()):
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    lookup = subparsers.add_parser("lookup")
    lookup.add_argument("--repository", required=True)
    lookup.add_argument("--run-id", required=True)
    lookup.add_argument("--name", required=True)
    lookup.add_argument("--token-env", required=True)
    lookup.add_argument("--job-output", required=True)
    reference = subparsers.add_parser("build-reference")
    reference.add_argument("--repository", required=True)
    reference.add_argument("--run-id", required=True)
    reference.add_argument("--artifact-id", required=True)
    reference.add_argument("--artifact-digest", required=True)
    reference.add_argument("--report-name", required=True)
    reference.add_argument("--output", required=True)
    verify = subparsers.add_parser("verify-reference")
    verify.add_argument("--reference", required=True)
    verify.add_argument("--expected-report-sha256", required=True)
    verify.add_argument("--token-env", required=True)
    verify.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.action == "lookup":
            token = os.environ.get(args.token_env, "").strip()
            with httpx.Client(timeout=20.0) as client:
                result = lookup_artifact(
                    repository=args.repository,
                    run_id=args.run_id,
                    name=args.name,
                    token=token,
                    client=client,
                )
            _write_job_output(Path(args.job_output), result)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.action == "build-reference":
            built = build_reference(
                repository=args.repository,
                run_id=args.run_id,
                artifact_id=args.artifact_id,
                artifact_digest=args.artifact_digest,
                report_name=args.report_name,
            )
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{built}\n")
            print(json.dumps({"output": str(output)}, sort_keys=True))
            return 0
        token = os.environ.get(args.token_env, "").strip()
        with httpx.Client(timeout=20.0) as client:
            verified = verify_reference(
                args.reference,
                expected_report_sha256=args.expected_report_sha256,
                token=token,
                client=client,
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(verified, handle, sort_keys=True, indent=2)
            handle.write("\n")
        print(json.dumps(verified, sort_keys=True))
        return 0
    except (GitHubArtifactError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
