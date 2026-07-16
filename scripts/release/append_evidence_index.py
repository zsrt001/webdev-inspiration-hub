#!/usr/bin/env python3
"""Append one manifest-bound evidence entry without mutating the manifest."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ENTRY_FIELDS = frozenset(
    {
        "schema",
        "manifest_sha256",
        "evidence_type",
        "case_id",
        "run_id",
        "attempt",
        "deployment_id",
        "report_sha256",
        "produced_at",
        "observed_at",
        "freshness_result",
        "reviewer_approval_ref",
        "decision",
    }
)
DECISIONS = frozenset({"PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


def _identifier(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _timestamp(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
        raise ValueError("evidence entry must contain the exact allowlisted fields")
    if entry.get("schema") != "vowpic.evidence-entry.v1":
        raise ValueError("evidence entry schema is unsupported")
    normalized = dict(entry)
    for field in ("manifest_sha256", "report_sha256"):
        digest = str(entry[field] or "").strip().lower()
        if not _HEX64.fullmatch(digest):
            raise ValueError(f"{field} is invalid")
        normalized[field] = digest
    for field in (
        "evidence_type",
        "case_id",
        "run_id",
        "deployment_id",
        "reviewer_approval_ref",
    ):
        normalized[field] = _identifier(entry[field], label=field)
    if not isinstance(entry["attempt"], int) or isinstance(entry["attempt"], bool) or entry["attempt"] < 1:
        raise ValueError("attempt must be a positive integer")
    normalized["produced_at"] = _timestamp(entry["produced_at"], label="produced_at")
    normalized["observed_at"] = _timestamp(entry["observed_at"], label="observed_at")
    if entry["freshness_result"] not in {"PASS", "FAIL"}:
        raise ValueError("freshness_result is invalid")
    if entry["decision"] not in DECISIONS:
        raise ValueError("decision is invalid")
    return normalized


def _canonical_line(entry: dict[str, Any]) -> bytes:
    return (json.dumps(validate_entry(entry), sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def append_evidence(
    index_path: Path,
    *,
    manifest_path: Path,
    report_path: Path,
    entry: dict[str, Any],
) -> None:
    normalized = validate_entry(entry)
    manifest_raw = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if normalized["manifest_sha256"] != manifest_sha:
        raise ValueError("evidence entry does not bind the exact manifest bytes")
    if normalized["report_sha256"] != hashlib.sha256(report_path.read_bytes()).hexdigest():
        raise ValueError("evidence entry does not bind the exact report bytes")

    existing: list[dict[str, Any]] = []
    if index_path.exists():
        for line_number, line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                existing.append(validate_entry(json.loads(line)))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"existing evidence index line {line_number} is invalid") from exc
    if any(item["case_id"] == normalized["case_id"] for item in existing):
        raise ValueError("duplicate or conflicting evidence case ID")
    if any(item["manifest_sha256"] != manifest_sha for item in existing):
        raise ValueError("evidence index contains another manifest identity")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("ab") as handle:
        handle.write(_canonical_line(normalized))
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--entry", required=True)
    args = parser.parse_args()
    try:
        entry = json.loads(Path(args.entry).read_text(encoding="utf-8"))
        append_evidence(
            Path(args.index),
            manifest_path=Path(args.manifest),
            report_path=Path(args.report),
            entry=entry,
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
