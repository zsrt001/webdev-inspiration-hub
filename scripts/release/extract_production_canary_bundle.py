#!/usr/bin/env python3
"""Safely extract one protected Production capability-canary input bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from typing import Any


CAPABILITIES = (
    "google_auth",
    "authenticated_upload",
    "credit_pack_checkout",
    "subscription_billing",
    "generation",
    "private_download",
    "partner_invite",
)
REQUIRED_FILES = frozenset(f"{capability}.json" for capability in CAPABILITIES)
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_MEMBERS = 40
SAFE_ASSET_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _runner_temp() -> Path:
    value = os.environ.get("RUNNER_TEMP", "").strip()
    if not value:
        raise ValueError("RUNNER_TEMP is required for Production canary extraction")
    return Path(value).resolve(strict=True)


def _private_path(path: Path, *, label: str, runner_temp: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved == runner_temp or runner_temp not in resolved.parents:
        raise ValueError(f"{label} must stay below RUNNER_TEMP")
    return resolved


def _member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/"):
        raise ValueError("Production canary archive member path is invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Production canary archive member escaped its root")
    if len(path.parts) == 1:
        if path.name not in REQUIRED_FILES:
            raise ValueError("Production canary archive contains an unexpected root file")
        return path
    if (
        len(path.parts) != 2
        or path.parts[0] != "assets"
        or not SAFE_ASSET_PART.fullmatch(path.parts[1])
    ):
        raise ValueError("Production canary archive asset path is not allowlisted")
    return path


def _validate_canary_input(path: Path, capability: str) -> str:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Production canary input {capability} is not an object")
    if (
        payload.get("schema") != "vowpic.production-capability-canary-input.v1"
        or payload.get("capability") != capability
    ):
        raise ValueError(f"Production canary input {capability} coordinates are invalid")
    return hashlib.sha256(raw).hexdigest()


def extract_bundle(archive: Path, output_directory: Path) -> dict[str, Any]:
    runner_temp = _runner_temp()
    archive = _private_path(archive, label="Production canary archive", runner_temp=runner_temp)
    output_directory = _private_path(
        output_directory,
        label="Production canary output directory",
        runner_temp=runner_temp,
    )
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("Production canary archive must be one regular file")
    archive_size = archive.stat().st_size
    if archive_size < 1 or archive_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Production canary archive size is invalid")
    output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(output_directory.iterdir()):
        raise ValueError("Production canary output directory must be empty")

    extracted_names: set[str] = set()
    total_size = 0
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = bundle.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ValueError("Production canary archive member count is invalid")
        for member in members:
            if member.isdir():
                directory = PurePosixPath(member.name)
                if directory.parts != ("assets",):
                    raise ValueError("Production canary archive directory is not allowlisted")
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError("Production canary archive contains a non-regular member")
            relative = _member_path(member.name)
            normalized = relative.as_posix()
            if normalized in extracted_names:
                raise ValueError("Production canary archive contains duplicate members")
            if member.size < 1 or member.size > MAX_MEMBER_BYTES:
                raise ValueError("Production canary archive member size is invalid")
            total_size += member.size
            if total_size > MAX_TOTAL_BYTES:
                raise ValueError("Production canary archive expanded size is too large")
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError("Production canary archive member cannot be read")
            target = (output_directory / Path(*relative.parts)).resolve(strict=False)
            if output_directory not in target.parents:
                raise ValueError("Production canary archive target escaped its root")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = source.read(MAX_MEMBER_BYTES + 1)
            if len(raw) != member.size or len(raw) > MAX_MEMBER_BYTES:
                raise ValueError("Production canary archive member length drifted")
            with target.open("xb") as handle:
                handle.write(raw)
            os.chmod(target, 0o600)
            extracted_names.add(normalized)

    missing = REQUIRED_FILES - extracted_names
    if missing:
        raise ValueError(
            "Production canary archive is missing required inputs: "
            + ", ".join(sorted(missing))
        )
    hashes = {
        capability: _validate_canary_input(
            output_directory / f"{capability}.json", capability
        )
        for capability in CAPABILITIES
    }
    return {
        "schema": "vowpic.production-canary-bundle-extraction.v1",
        "passed": True,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "member_count": len(extracted_names),
        "expanded_bytes": total_size,
        "capability_input_sha256": hashes,
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    runner_temp = _runner_temp()
    target = _private_path(path, label="Production canary extraction report", runner_temp=runner_temp)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    os.chmod(target, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = extract_bundle(Path(args.archive), Path(args.output_directory))
        _write_create_once(Path(args.output), report)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
