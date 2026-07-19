#!/usr/bin/env python3
"""Shared private-evidence and collector-proof primitives for COMMERCIAL_7A."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import UUID


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
SIGNATURE = re.compile(r"^hmac-sha256:([0-9a-f]{64})$")
SENSITIVE_KEY = re.compile(
    r"(email|token|cookie|password|secret|authorization|raw_url|object_key|permanent_url)",
    re.IGNORECASE,
)
JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def canonical(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical(value[key])}"
            for key in sorted(value)
        ) + "}"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, datetime):
        return json.dumps(value.isoformat(), ensure_ascii=False)
    if isinstance(value, (UUID, Decimal)):
        return json.dumps(str(value), ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def reject_sensitive_evidence(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_sensitive_evidence(item, (*path, str(index)))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            is_denial = item is True and str(key).endswith("_denied")
            if SENSITIVE_KEY.search(str(key)) and not is_denial:
                raise ValueError(
                    "sensitive evidence key is forbidden: " + ".".join((*path, str(key)))
                )
            reject_sensitive_evidence(item, (*path, str(key)))
        return
    if isinstance(value, str) and (JWT.search(value) or EMAIL.search(value)):
        raise ValueError("sensitive evidence value is forbidden: " + ".".join(path))


def _runner_temp() -> Path:
    value = os.environ.get("RUNNER_TEMP", "").strip()
    if not value:
        raise ValueError("RUNNER_TEMP is required for acceptance evidence")
    return Path(value).resolve(strict=True)


def private_path(candidate: Path, *, label: str, must_exist: bool = True) -> Path:
    runner_temp = _runner_temp()
    resolved = candidate.resolve(strict=must_exist)
    if resolved == runner_temp or runner_temp not in resolved.parents:
        raise ValueError(f"{label} must stay below RUNNER_TEMP")
    return resolved


def read_private_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    resolved = private_path(path, label=label)
    stat = resolved.lstat()
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or stat.st_size < 2
        or stat.st_size > 1_000_000
    ):
        raise ValueError(f"{label} must be one bounded regular file")
    if os.name != "nt" and stat.st_mode & 0o077:
        raise ValueError(f"{label} must be mode 0600")
    raw = resolved.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, raw


def signing_key(environment_name: str = "ACCEPTANCE_EVIDENCE_SIGNING_KEY") -> bytes:
    key = os.environ.get(environment_name, "").encode("utf-8")
    if len(key) < 32:
        raise ValueError(f"{environment_name} must contain at least 32 bytes")
    return key


def verify_signed_report(
    report: dict[str, Any],
    *,
    key: bytes,
    expected: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    match = SIGNATURE.fullmatch(str(report.get("signature") or ""))
    unsigned = {name: value for name, value in report.items() if name != "signature"}
    wanted = hmac.new(
        key, canonical(unsigned).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not match or not hmac.compare_digest(match.group(1), wanted):
        raise ValueError(f"{label} signature is invalid")
    for field, value in expected.items():
        if report.get(field) != value:
            raise ValueError(f"{label} {field} mismatch")
    reject_sensitive_evidence(unsigned)
    return unsigned


def validate_release_binding(payload: dict[str, Any]) -> None:
    if not SHA40.fullmatch(str(payload.get("source_sha") or "")):
        raise ValueError("acceptance source SHA is invalid")
    if not RUNTIME_ID.fullmatch(str(payload.get("runtime_bundle_id") or "")):
        raise ValueError("acceptance runtime bundle ID is invalid")
    if not SHA64.fullmatch(str(payload.get("manifest_sha256") or "")):
        raise ValueError("acceptance manifest SHA-256 is invalid")
    if not SHA64.fullmatch(str(payload.get("user_subject_hmac_sha256") or "")):
        raise ValueError("ordinary-user subject HMAC is invalid")
    deployment_id = str(payload.get("deployment_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", deployment_id):
        raise ValueError("acceptance deployment ID is invalid")


def seal_collected_input(
    payload: dict[str, Any],
    *,
    phase: str,
    browser_report_sha256: str,
    database_facts: Any,
    key: bytes,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    if "collector" in payload:
        raise ValueError("unsealed acceptance payload must not contain collector")
    validate_release_binding(payload)
    if not SHA64.fullmatch(browser_report_sha256):
        raise ValueError("browser report SHA-256 is invalid")
    reject_sensitive_evidence(payload)
    unsigned_proof = {
        "schema": "vowpic.acceptance-collector-proof.v1",
        "phase": phase,
        "source_sha": payload["source_sha"],
        "runtime_bundle_id": payload["runtime_bundle_id"],
        "deployment_id": payload["deployment_id"],
        "manifest_sha256": payload["manifest_sha256"],
        "browser_report_sha256": browser_report_sha256,
        "database_facts_sha256": canonical_sha256(database_facts),
        "collected_at": (collected_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "input_sha256": canonical_sha256(payload),
    }
    signature = hmac.new(
        key, canonical(unsigned_proof).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    sealed = {
        **payload,
        "collector": {
            **unsigned_proof,
            "signature": f"hmac-sha256:{signature}",
        },
    }
    reject_sensitive_evidence(sealed)
    return sealed


def write_private_create_once(path: Path, payload: dict[str, Any]) -> None:
    target = private_path(path, label="acceptance collector output", must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_sensitive_evidence(payload)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(payload) + "\n")
    os.chmod(target, 0o600)
