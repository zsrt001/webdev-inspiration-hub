"""Produce a byte-stable identity for the exact Git worktree under test."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


FINGERPRINT_VERSION = b"vowpic-worktree-fingerprint.v1\0"


def _git(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _append_length_prefixed(hasher: Any, value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, byteorder="big", signed=False))
    hasher.update(value)


def get_worktree_identity(root: Path) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    base_sha_bytes = _git(resolved_root, "rev-parse", "HEAD").strip()
    try:
        base_sha = base_sha_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Git HEAD is not ASCII") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("Git HEAD is not a full 40-character SHA")

    status = _git(
        resolved_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
    )
    if not status:
        return {
            "base_sha": base_sha,
            "source_sha": base_sha,
            "code_identity": "CLEAN_COMMIT",
            "working_tree_clean": True,
            "working_tree_sha256": None,
            "release_eligible": True,
        }

    tracked_diff = _git(
        resolved_root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        ".",
    )
    raw_untracked_paths = [
        value
        for value in _git(
            resolved_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        if value
    ]

    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_VERSION)
    _append_length_prefixed(hasher, base_sha_bytes)
    _append_length_prefixed(hasher, tracked_diff)
    hasher.update(len(raw_untracked_paths).to_bytes(8, byteorder="big", signed=False))
    for raw_path in sorted(raw_untracked_paths):
        relative_path = os.fsdecode(raw_path)
        object_hash = _git(
            resolved_root,
            "hash-object",
            "--no-filters",
            "--",
            relative_path,
        ).strip()
        if not re.fullmatch(rb"[0-9a-f]{40}", object_hash):
            raise ValueError("Git returned an invalid untracked object hash")
        _append_length_prefixed(hasher, raw_path)
        _append_length_prefixed(hasher, object_hash)

    return {
        "base_sha": base_sha,
        "source_sha": None,
        "code_identity": "UNCOMMITTED_WORKTREE",
        "working_tree_clean": False,
        "working_tree_sha256": hasher.hexdigest(),
        "release_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = get_worktree_identity(args.root)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
