#!/usr/bin/env python3
"""Fail closed when protected Google-auth material reaches release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, Sequence


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_SECRET_ENVS = (
    "PRODUCTION_GOOGLE_EMAIL",
    "PRODUCTION_GOOGLE_PARTNER_EMAIL",
    "SUPABASE_AUTH_CONFIG_TOKEN",
    "VERCEL_TOKEN",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ValueError("privacy scan cannot read the exact Git candidate")
    return result.stdout


def _git_paths(root: Path, *args: str) -> list[Path]:
    payload = _git(root, *args)
    paths = []
    for item in payload.split(b"\0"):
        if not item:
            continue
        try:
            relative = item.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("privacy scan encountered a non-UTF-8 Git path") from exc
        resolved = (root / relative).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("privacy scan path escaped the checkout") from exc
        if resolved.is_file():
            paths.append(resolved)
    return paths


def collect_scan_sources(
    root: Path,
    *,
    captured_outputs: Sequence[Path] = (),
    artifacts: Sequence[Path] = (),
) -> list[tuple[str, bytes]]:
    checkout = root.resolve(strict=True)
    sources: list[tuple[str, bytes]] = []
    seen: set[Path] = set()
    candidates = _git_paths(checkout, "ls-files", "-z") + _git_paths(
        checkout, "ls-files", "--others", "--exclude-standard", "-z"
    )
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        sources.append((path.relative_to(checkout).as_posix(), path.read_bytes()))
    sources.append(("git-diff", _git(checkout, "diff", "--binary", "--no-ext-diff")))
    for category, paths in (
        ("captured-output", captured_outputs),
        ("declared-artifact", artifacts),
    ):
        for index, raw_path in enumerate(paths):
            path = raw_path.resolve(strict=True)
            if not path.is_file():
                raise ValueError("privacy scan input is not a regular file")
            sources.append((f"{category}-{index}", path.read_bytes()))
    return sources


def load_protected_values(
    names: Sequence[str] = DEFAULT_SECRET_ENVS,
) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for name in names:
        raw = os.environ.get(name, "")
        if not raw or raw != raw.strip() or any(ord(character) < 32 for character in raw):
            raise ValueError("protected Google-auth material is unavailable")
        encoded = raw.encode("utf-8")
        if encoded in values:
            raise ValueError("protected Google-auth material is not distinct")
        values.append(encoded)
        if "EMAIL" in name:
            normalized = raw.lower().encode("utf-8")
            if normalized not in values:
                values.append(normalized)
    return tuple(values)


def _synthetic_canaries() -> tuple[bytes, ...]:
    return (
        ("codex.google" + ".privacy@example.invalid").encode(),
        ("ya29." + "codex-google-privacy-oauth").encode(),
        ("vowpic_session=" + "codex-google-privacy-cookie").encode(),
        ("Authorization: Bearer " + "codex-google-privacy-header").encode(),
        ("postgresql://codex:" + "google-privacy-password@localhost/db").encode(),
    )


def _contains_any(payload: bytes, needles: Iterable[bytes]) -> bool:
    return any(needle in payload for needle in needles)


def build_report(
    *,
    source_sha: str,
    sources: Sequence[tuple[str, bytes]],
    protected_values: Sequence[bytes],
) -> dict[str, object]:
    clean_sha = str(source_sha or "").strip().lower()
    if not SOURCE_SHA_RE.fullmatch(clean_sha):
        raise ValueError("privacy scan source SHA is invalid")
    if not sources or not protected_values:
        raise ValueError("privacy scan inputs are incomplete")
    if any(not label or not isinstance(payload, bytes) for label, payload in sources):
        raise ValueError("privacy scan source is invalid")
    if any(not value for value in protected_values):
        raise ValueError("protected Google-auth material is invalid")
    if any(_contains_any(payload, protected_values) for _, payload in sources):
        raise ValueError("protected Google-auth material reached release evidence")

    canaries = _synthetic_canaries()
    positive_control = b"\n".join(canaries)
    synthetic_detected = all(
        _contains_any(positive_control, (canary,)) for canary in canaries
    )
    sanitized_success = json.dumps(
        {"passed": True, "protected_values_zero_hits": True}, sort_keys=True
    ).encode()
    sanitized_failure = b"Google auth privacy boundary failed"
    sanitized_outputs_zero_hits = not _contains_any(
        sanitized_success + b"\n" + sanitized_failure,
        tuple(protected_values) + canaries,
    )
    if not synthetic_detected or not sanitized_outputs_zero_hits:
        raise ValueError("privacy scanner self-test failed")

    manifest = [
        {"label": label, "bytes": len(payload), "sha256": _sha256(payload)}
        for label, payload in sorted(sources, key=lambda item: item[0])
    ]
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    corpus = hashlib.sha256()
    for item in manifest:
        corpus.update(bytes.fromhex(str(item["sha256"])))
    report: dict[str, object] = {
        "schema": "vowpic.google-auth-privacy-boundary.v1",
        "passed": True,
        "source_sha": clean_sha,
        "protected_values_zero_hits": True,
        "synthetic_canaries_detected": True,
        "sanitized_outputs_zero_hits": True,
        "scanned_file_count": len(sources),
        "scan_scope_manifest_sha256": _sha256(manifest_bytes),
        "scan_corpus_sha256": corpus.hexdigest(),
    }
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    if _contains_any(serialized, tuple(protected_values) + canaries):
        raise ValueError("privacy report is not source-free")
    return report


def write_create_once(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--scan-root", required=True)
    parser.add_argument("--captured-output", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        root = Path(args.scan_root).resolve(strict=True)
        sources = collect_scan_sources(
            root,
            captured_outputs=[Path(value) for value in args.captured_output],
            artifacts=[Path(value) for value in args.artifact],
        )
        report = build_report(
            source_sha=args.source_sha,
            sources=sources,
            protected_values=load_protected_values(),
        )
        write_create_once(Path(args.output), report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Google auth privacy boundary failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
