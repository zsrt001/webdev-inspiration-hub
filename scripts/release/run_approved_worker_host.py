#!/usr/bin/env python3
"""Invoke only a reviewed, checksum-pinned Production Worker host adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


NOT_RUN_EXIT = 3
SCHEMA = "vowpic.worker-host-contract.v1"
RESPONSE_SCHEMA = "vowpic.worker-host-response.v1"
REPORT_SCHEMA = "vowpic.worker-host-adapter-report.v1"
ALLOWED_ACTIONS = (
    "build-push",
    "resolve-digest",
    "deploy-suspended",
    "start",
    "stop",
    "set-dispatch",
    "heartbeat",
    "ensure-running",
    "rollback",
    "reconcile-failure",
    "verify-no-release",
    "inject-secrets",
    "logs",
    "arm-response-drop-once",
    "inspect-response-drop",
    "disarm-response-drop",
)
_TOP_LEVEL_KEYS = {
    "schema",
    "contract_version",
    "approved",
    "status",
    "provider",
    "executable",
    "executable_sha256",
    "actions",
    "secret_env_allowlist",
    "controls",
    "approval",
}
_CONTROL_VALUES = {
    "argument_array_only": True,
    "mutable_image_tags_forbidden": True,
    "dispatch_paused_before_fault_arm": True,
    "one_submit_limit": 1,
    "maximum_fault_ttl_seconds": 300,
    "fault_intent_idempotency_keyed": True,
    "disarm_tombstone_required": True,
    "late_arm_after_tombstone_forbidden": True,
    "runtime_scoped_fault_rule": True,
    "request_bytes_unchanged": True,
}
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUNTIME = re.compile(r"^rtb_[0-9a-f]{64}$")
_FLAG = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_SAFE_COORDINATE = re.compile(r"^[A-Za-z0-9_.:@/+\\=-]{1,512}$")
_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|cookie|authorization|raw_url|object_key|email)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


class WorkerHostNotRun(ValueError):
    """The reviewed external host contract is not available."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_contract(path: Path, *, require_approved: bool) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("Worker host contract fields are not exact")
    if payload.get("schema") != SCHEMA:
        raise ValueError("Worker host contract schema is invalid")
    actions = payload.get("actions")
    if not isinstance(actions, dict) or set(actions) != set(ALLOWED_ACTIONS):
        raise ValueError("Worker host action set is incomplete or unexpected")
    for name, specification in actions.items():
        if (
            not isinstance(specification, dict)
            or set(specification) != {"required_cli", "optional_cli"}
        ):
            raise ValueError(f"Worker host action contract is invalid: {name}")
        required = specification["required_cli"]
        optional = specification["optional_cli"]
        if (
            not isinstance(required, list)
            or not isinstance(optional, list)
            or len(set(required + optional)) != len(required + optional)
            or any(not isinstance(flag, str) or not _FLAG.fullmatch(flag) for flag in required + optional)
        ):
            raise ValueError(f"Worker host CLI allowlist is invalid: {name}")
    if payload.get("controls") != _CONTROL_VALUES:
        raise ValueError("Worker host safety controls are incomplete")
    env_names = payload.get("secret_env_allowlist")
    if (
        not isinstance(env_names, list)
        or len(set(env_names)) != len(env_names)
        or any(not isinstance(name, str) or not _ENV_NAME.fullmatch(name) for name in env_names)
    ):
        raise ValueError("Worker host secret environment allowlist is invalid")
    approval = payload.get("approval")
    if not isinstance(approval, dict) or set(approval) != {"state", "reference", "approved_at"}:
        raise ValueError("Worker host approval record is invalid")
    digest = _sha256_bytes(raw)
    if require_approved:
        if (
            payload.get("approved") is not True
            or payload.get("status") != "APPROVED"
            or approval.get("state") != "APPROVED"
            or not str(approval.get("reference") or "").strip()
            or not str(approval.get("approved_at") or "").strip()
        ):
            raise WorkerHostNotRun("Worker host contract has not been approved")
        if not str(payload.get("provider") or "").strip():
            raise WorkerHostNotRun("Worker host provider has not been approved")
        executable = Path(str(payload.get("executable") or ""))
        expected_executable_hash = str(payload.get("executable_sha256") or "").lower()
        if not executable.is_absolute() or not executable.is_file():
            raise WorkerHostNotRun("approved Worker host executable is unavailable")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_executable_hash):
            raise ValueError("Worker host executable SHA-256 is invalid")
        if _sha256_bytes(executable.read_bytes()) != expected_executable_hash:
            raise ValueError("Worker host executable checksum mismatch")
    return payload, digest


def parse_invocation(argv: list[str]) -> tuple[str, dict[str, str | bool]]:
    if not argv or argv[0] in {"-h", "--help"}:
        raise WorkerHostNotRun(
            "usage: run_approved_worker_host.py ACTION --contract FILE --output FILE [allowlisted flags]"
        )
    action = argv[0]
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Worker host action is not allowlisted")
    values: dict[str, str | bool] = {}
    index = 1
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--") or not _FLAG.fullmatch(token[2:]):
            raise ValueError("Worker host arguments must use --name value form")
        name = token[2:]
        if name in values:
            raise ValueError(f"duplicate Worker host argument: {name}")
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            values[name] = True
            index += 1
        else:
            value = argv[index + 1]
            if "\n" in value or "\r" in value or "\x00" in value:
                raise ValueError(f"invalid Worker host argument value: {name}")
            values[name] = value
            index += 2
    return action, values


def validate_action_inputs(
    contract: dict[str, Any],
    action: str,
    values: dict[str, str | bool],
) -> dict[str, str | bool]:
    global_names = {"contract", "output", "job-env", "env-prefix"}
    specification = contract["actions"][action]
    required = set(specification["required_cli"])
    optional = set(specification["optional_cli"])
    action_values = {key: value for key, value in values.items() if key not in global_names}
    missing = required - set(action_values)
    unexpected = set(action_values) - required - optional
    if missing:
        raise ValueError(f"missing Worker host inputs: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"unexpected Worker host inputs: {', '.join(sorted(unexpected))}")
    for name, value in action_values.items():
        if value is True:
            if name not in optional:
                raise ValueError(f"Worker host input requires a value: {name}")
            continue
        clean = str(value).strip()
        if not clean or len(clean) > 512 or not _SAFE_COORDINATE.fullmatch(clean):
            raise ValueError(f"Worker host input is invalid: {name}")
        if name == "source-sha" and not _SOURCE_SHA.fullmatch(clean):
            raise ValueError("Worker source SHA is invalid")
        if name in {"image-digest", "expected-image-digest"} and not _DIGEST.fullmatch(clean):
            raise ValueError("Worker image must use an immutable digest")
        if name in {"runtime-bundle-id", "expected-runtime-bundle-id"} and not _RUNTIME.fullmatch(clean):
            raise ValueError("Worker runtime bundle ID is invalid")
        if name == "dispatch-mode" and clean not in {"enabled", "disabled"}:
            raise ValueError("Worker dispatch mode is invalid")
    return action_values


def _reject_sensitive(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError(f"Worker host response contains a sensitive field: {'.'.join((*path, str(key)))}")
            _reject_sensitive(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive(child, (*path, str(index)))
    elif isinstance(value, str) and (_EMAIL.search(value) or _JWT.search(value)):
        raise ValueError("Worker host response contains a sensitive value")


def validate_host_response(payload: Any, *, action: str) -> dict[str, Any]:
    required = {"schema", "action", "passed", "state", "coordinates", "observed_at"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Worker host response fields are not exact")
    if (
        payload.get("schema") != RESPONSE_SCHEMA
        or payload.get("action") != action
        or payload.get("passed") is not True
        or not isinstance(payload.get("coordinates"), dict)
        or not str(payload.get("state") or "").strip()
    ):
        raise ValueError("Worker host response did not prove the requested action")
    observed_at = datetime.fromisoformat(
        str(payload["observed_at"]).replace("Z", "+00:00")
    )
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Worker host response timestamp must be timezone-aware")
    coordinates = payload["coordinates"]
    if "worker_image_digest" in coordinates and not _DIGEST.fullmatch(
        str(coordinates["worker_image_digest"])
    ):
        raise ValueError("Worker host response image digest is invalid")
    if "runtime_bundle_id" in coordinates and not _RUNTIME.fullmatch(
        str(coordinates["runtime_bundle_id"])
    ):
        raise ValueError("Worker host response runtime bundle ID is invalid")
    _reject_sensitive(payload)
    return payload


def _safe_environment(contract: dict[str, Any]) -> dict[str, str]:
    base_names = ("LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
    result = {name: os.environ[name] for name in base_names if os.environ.get(name)}
    for name in contract["secret_env_allowlist"]:
        value = os.environ.get(name)
        if not value:
            raise WorkerHostNotRun(f"approved Worker host secret environment is missing: {name}")
        result[name] = value
    return result


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_bytes(payload).decode("utf-8"))
        handle.write("\n")


def _write_job_env(path: Path | None, prefix: str, report: dict[str, Any]) -> None:
    if path is None:
        return
    clean_prefix = str(prefix or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}_", clean_prefix):
        raise ValueError("Worker host environment prefix is invalid")
    coordinates = report["coordinates"]
    exports = {
        key.upper(): str(value)
        for key, value in coordinates.items()
        if key in {"worker_image_digest", "worker_deployment_id", "runtime_bundle_id"}
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for name, value in sorted(exports.items()):
            if "\n" in value or "\r" in value:
                raise ValueError("Worker host environment export is invalid")
            handle.write(f"{clean_prefix}{name}={value}\n")


def run(argv: list[str]) -> int:
    action, values = parse_invocation(argv)
    if not values.get("contract") or not values.get("output"):
        raise ValueError("--contract and --output are required")
    contract_path = Path(str(values.get("contract") or ""))
    output_path = Path(str(values.get("output") or ""))
    contract, contract_sha256 = load_contract(contract_path, require_approved=True)
    action_inputs = validate_action_inputs(contract, action, values)
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or ".tmp/worker-host").resolve()
    runner_temp.mkdir(parents=True, exist_ok=True)
    request_path = runner_temp / f"worker-host-{action}-request.json"
    response_path = runner_temp / f"worker-host-{action}-response.json"
    if request_path.exists() or response_path.exists():
        raise ValueError("Worker host invocation files already exist")
    request = {
        "schema": "vowpic.worker-host-request.v1",
        "action": action,
        "contract_sha256": contract_sha256,
        "inputs": action_inputs,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_create_once(request_path, request)
    executable = str(contract["executable"])
    command = [
        executable,
        action,
        "--request-file",
        str(request_path),
        "--response-file",
        str(response_path),
    ]
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=_safe_environment(contract),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"Worker host action failed with exit code {completed.returncode}")
    if not response_path.is_file() or response_path.stat().st_size > 1_000_000:
        raise ValueError("Worker host response file is missing or oversized")
    response_raw = response_path.read_bytes()
    response = validate_host_response(
        json.loads(response_raw.decode("utf-8")),
        action=action,
    )
    signing_key = os.environ.get("WORKER_HOST_EVIDENCE_SIGNING_KEY", "").encode("utf-8")
    if len(signing_key) < 32:
        raise WorkerHostNotRun("Worker host evidence signing key is unavailable")
    unsigned = {
        "schema": REPORT_SCHEMA,
        "passed": True,
        "action": action,
        "contract_sha256": contract_sha256,
        "request_sha256": _sha256_bytes(_canonical_bytes(request)),
        "host_response_sha256": _sha256_bytes(response_raw),
        "state": response["state"],
        "coordinates": response["coordinates"],
        "observed_at": response["observed_at"],
    }
    signature = hmac.new(signing_key, _canonical_bytes(unsigned), hashlib.sha256).hexdigest()
    report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
    _write_create_once(output_path, report)
    _write_job_env(
        Path(str(values["job-env"])) if values.get("job-env") else None,
        str(values.get("env-prefix") or "WORKER_"),
        report,
    )
    request_path.unlink(missing_ok=True)
    response_path.unlink(missing_ok=True)
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except WorkerHostNotRun as exc:
        print(f"NOT_RUN: {exc}", file=sys.stderr)
        return NOT_RUN_EXIT
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        message = _EMAIL.sub("[EMAIL_REDACTED]", str(exc))
        message = _JWT.sub("[TOKEN_REDACTED]", message)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
