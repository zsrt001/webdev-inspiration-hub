#!/usr/bin/env python3
"""Deploy and control the production ARQ Worker through pinned Railway CLI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit


SCHEMA = "vowpic.worker-host-contract.v2"
REPORT_SCHEMA = "vowpic.worker-host-report.v2"
NOT_RUN_EXIT = 3
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
_COORDINATE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_REGIONS = frozenset({"us-west", "us-east", "eu-west", "southeast-asia"})
_TOP_LEVEL_KEYS = {
    "schema",
    "provider",
    "cli",
    "coordinates",
    "deployment",
    "runtime_secret_envs",
    "runtime_optional_envs",
    "controls",
}
_CLI_KEYS = {"version", "archive_url", "archive_sha256", "executable_sha256"}
_COORDINATE_KEYS = {
    "token_env",
    "project_env",
    "environment_env",
    "service_env",
    "region_env",
    "cli_path_env",
}
_DEPLOYMENT_KEYS = {
    "registry",
    "image_repository",
    "dockerfile_path",
    "source_label",
    "restart_policy",
    "draining_seconds",
}
_CONTROL_VALUES = {
    "exact_source_checkout_required": True,
    "project_token_required": True,
    "secret_values_via_stdin_only": True,
    "interactive_project_creation_forbidden": True,
    "digest_pinned_image_source_required": True,
    "public_registry_pull_required": True,
    "feature_flags_off_before_deploy": True,
    "deployment_id_bound_heartbeat_required": True,
    "rollback_stops_worker_before_api_rollback": True,
}
_RUNTIME_SECRETS = (
    "ACCEPTANCE_IDENTITY_HMAC_KEY",
    "CONTROL_PLANE_DATABASE_URL",
    "DATABASE_URL",
    "EVOLINK_API_KEY",
    "PRIVATE_BLOB_READ_WRITE_TOKEN",
    "REDIS_URL",
    "SECRET_KEY",
    "WENWEN_VISION_API_KEY",
)
_RUNTIME_OPTIONAL = (
    "EVOLINK_API_BASE_URL",
    "EVOLINK_IMAGE_MODEL",
    "EVOLINK_IMAGE_QUALITY",
    "EVOLINK_IMAGE_SIZE",
    "WENWEN_API_BASE_URL",
)
_REPORT_KEYS = {
    "schema",
    "passed",
    "action",
    "provider",
    "contract_sha256",
    "state",
    "coordinates",
    "observed_at",
    "signature",
}


class WorkerHostNotRun(RuntimeError):
    """Required Railway configuration is absent, so no external action ran."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify_report(
    payload: Any,
    *,
    action: str,
    signing_key: bytes,
    contract_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _REPORT_KEYS:
        raise ValueError("Worker host report fields are not exact")
    signature = str(payload.get("signature") or "")
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    if (
        payload.get("schema") != REPORT_SCHEMA
        or payload.get("passed") is not True
        or payload.get("action") != action
        or payload.get("provider") != "railway"
        or not _HEX64.fullmatch(str(payload.get("contract_sha256") or ""))
        or not str(payload.get("state") or "").strip()
        or not isinstance(payload.get("coordinates"), dict)
        or len(signing_key) < 32
    ):
        raise ValueError("Worker host report identity is invalid")
    if contract_sha256 and payload["contract_sha256"] != contract_sha256:
        raise ValueError("Worker host report contract digest mismatch")
    expected = "hmac-sha256:" + hmac.new(
        signing_key, _canonical_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Worker host report signature is invalid")
    observed_at = datetime.fromisoformat(
        str(payload.get("observed_at") or "").replace("Z", "+00:00")
    )
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Worker host report timestamp must be timezone-aware")
    return payload


def load_contract(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("Worker host contract fields are not exact")
    if payload.get("schema") != SCHEMA or payload.get("provider") != "railway":
        raise ValueError("Worker host contract provider/schema is invalid")

    cli = payload.get("cli")
    if not isinstance(cli, dict) or set(cli) != _CLI_KEYS:
        raise ValueError("Worker host CLI contract is invalid")
    if cli.get("version") != "5.27.2":
        raise ValueError("Worker host CLI version is not pinned")
    if cli.get("archive_url") != (
        "https://github.com/railwayapp/cli/releases/download/v5.27.2/"
        "railway-v5.27.2-x86_64-unknown-linux-gnu.tar.gz"
    ):
        raise ValueError("Worker host CLI archive is not the reviewed official asset")
    for field in ("archive_sha256", "executable_sha256"):
        if not _HEX64.fullmatch(str(cli.get(field) or "")):
            raise ValueError(f"Worker host CLI {field} is invalid")

    coordinates = payload.get("coordinates")
    if not isinstance(coordinates, dict) or set(coordinates) != _COORDINATE_KEYS:
        raise ValueError("Worker host coordinate contract is invalid")
    if len(set(coordinates.values())) != len(coordinates):
        raise ValueError("Worker host coordinate environment names must be unique")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", str(value)) for value in coordinates.values()):
        raise ValueError("Worker host coordinate environment name is invalid")

    deployment = payload.get("deployment")
    if not isinstance(deployment, dict) or set(deployment) != _DEPLOYMENT_KEYS:
        raise ValueError("Worker host deployment contract is invalid")
    if deployment != {
        "registry": "ghcr.io",
        "image_repository": "ghcr.io/zsrt001/vowpic-worker",
        "dockerfile_path": "backend/Dockerfile.worker",
        "source_label": "https://github.com/zsrt001/webdev-inspiration-hub",
        "restart_policy": "ALWAYS",
        "draining_seconds": 30,
    }:
        raise ValueError("Worker host deployment contract drifted")
    if tuple(payload.get("runtime_secret_envs") or ()) != _RUNTIME_SECRETS:
        raise ValueError("Worker runtime secret allowlist drifted")
    if tuple(payload.get("runtime_optional_envs") or ()) != _RUNTIME_OPTIONAL:
        raise ValueError("Worker runtime optional allowlist drifted")
    if payload.get("controls") != _CONTROL_VALUES:
        raise ValueError("Worker host safety controls are incomplete")
    return payload, _sha256_bytes(raw)


def _required_coordinate(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not _COORDINATE.fullmatch(normalized):
        raise WorkerHostNotRun(f"{label} is not configured")
    return normalized


def runtime_context(
    contract: dict[str, Any], env: Mapping[str, str]
) -> dict[str, str]:
    names = contract["coordinates"]
    context = {
        "token": str(env.get(names["token_env"], "")).strip(),
        "project": _required_coordinate(env.get(names["project_env"]), "Railway project"),
        "environment": _required_coordinate(
            env.get(names["environment_env"]), "Railway environment"
        ),
        "service": _required_coordinate(env.get(names["service_env"]), "Railway service"),
        "region": _required_coordinate(env.get(names["region_env"]), "Railway region"),
        "cli_path": str(env.get(names["cli_path_env"], "")).strip(),
    }
    if len(context["token"]) < 20:
        raise WorkerHostNotRun("Railway project token is not configured")
    if context["region"] not in _REGIONS:
        raise WorkerHostNotRun("Railway region is not allowlisted")
    executable = Path(context["cli_path"])
    if not executable.is_absolute() or not executable.is_file():
        raise WorkerHostNotRun("pinned Railway CLI is unavailable")
    if _sha256_bytes(executable.read_bytes()) != contract["cli"]["executable_sha256"]:
        raise ValueError("Railway CLI executable checksum mismatch")
    return context


def _cli_environment(context: Mapping[str, str]) -> dict[str, str]:
    result = {
        name: os.environ[name]
        for name in ("LANG", "LC_ALL", "PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
        if os.environ.get(name)
    }
    result.update({"RAILWAY_TOKEN": context["token"], "CI": "true"})
    return result


def _run_cli(
    context: Mapping[str, str],
    arguments: list[str],
    *,
    stdin_value: str | None = None,
    timeout: int = 900,
) -> str:
    options: dict[str, Any] = {
        "cwd": Path.cwd(),
        "env": _cli_environment(context),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
        "timeout": timeout,
        "check": False,
    }
    if stdin_value is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = stdin_value
    completed = subprocess.run([context["cli_path"], *arguments], **options)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Railway CLI action failed with exit code {completed.returncode}"
        )
    return completed.stdout


def _target_args(context: Mapping[str, str], *, service: bool = True) -> list[str]:
    result = [
        "--project",
        context["project"],
        "--environment",
        context["environment"],
    ]
    if service:
        result.extend(["--service", context["service"]])
    return result


def _parse_json(value: str, *, label: str) -> Any:
    text = str(value or "").strip()
    if not text or len(text.encode("utf-8")) > 5_000_000:
        raise ValueError(f"Railway {label} JSON is missing or oversized")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Railway {label} output is not JSON")


def _deployment_rows(context: Mapping[str, str]) -> list[dict[str, Any]]:
    payload = _parse_json(
        _run_cli(
            context,
            ["deployment", "list", *_target_args(context), "--limit", "20", "--json"],
            timeout=60,
        ),
        label="deployment list",
    )
    if isinstance(payload, dict) and isinstance(payload.get("deployments"), list):
        payload = payload["deployments"]
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("Railway deployment list shape is invalid")
    return payload


def _deployment_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("deploymentId") or "").strip()


def _set_variable(
    context: Mapping[str, str], name: str, value: str
) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", name):
        raise ValueError("Worker variable name is invalid")
    if not value or "\x00" in value:
        raise WorkerHostNotRun(f"required Worker runtime variable is missing: {name}")
    _run_cli(
        context,
        [
            "variable",
            "set",
            name,
            "--stdin",
            "--skip-deploys",
            *_target_args(context),
            "--json",
        ],
        stdin_value=value,
        timeout=60,
    )


def _fixed_runtime_variables(args: argparse.Namespace) -> dict[str, str]:
    worker_digest = args.image_ref.rsplit("@", 1)[1]
    return {
        "ALLOW_MEMORY_FALLBACK": "false",
        "API_DEPLOYMENT_ID": args.api_deployment_id,
        "GENERATION_ENGINE": "evolink",
        "GATEKEEPER_ALLOW_WITHOUT_PILLOW": "false",
        "LLM_PROVIDER": "wenwen",
        "QA_ALLOW_WITHOUT_PILLOW": "false",
        "QA_REQUIRE_VISION": "true",
        "RELEASE_ROLE": "COMMERCIAL_7A",
        "RUNTIME_BUNDLE_ID": args.runtime_bundle_id,
        "RUNTIME_ENVIRONMENT": "production",
        "SOURCE_SHA": args.source_sha,
        "STORAGE_PROVIDER": "vercel",
        "TASK_EXECUTION_MODE": "arq",
        "WORKER_IMAGE_DIGEST": worker_digest,
    }


def _validate_release_inputs(args: argparse.Namespace) -> None:
    if not _SOURCE_SHA.fullmatch(str(args.source_sha or "")):
        raise ValueError("Worker source SHA is invalid")
    if not _RUNTIME_ID.fullmatch(str(args.runtime_bundle_id or "")):
        raise ValueError("Worker runtime bundle ID is invalid")
    _required_coordinate(args.api_deployment_id, "API deployment")


def _validate_image_ref(contract: Mapping[str, Any], value: object) -> str:
    image_ref = str(value or "").strip().lower()
    prefix = contract["deployment"]["image_repository"] + "@sha256:"
    if not image_ref.startswith(prefix) or not _HEX64.fullmatch(image_ref[len(prefix) :]):
        raise ValueError("Worker image must use the approved GHCR repository and OCI digest")
    return image_ref


def _base_coordinates(context: Mapping[str, str]) -> dict[str, str]:
    return {
        "provider": "railway",
        "project": context["project"],
        "environment": context["environment"],
        "service": context["service"],
        "region": context["region"],
    }


def _scale(context: Mapping[str, str], replicas: int) -> None:
    _run_cli(
        context,
        [
            "scale",
            f"{context['region']}={replicas}",
            *_target_args(context),
            "--json",
        ],
        timeout=120,
    )


def _preflight(context: Mapping[str, str]) -> tuple[str, dict[str, Any]]:
    _parse_json(
        _run_cli(
            context,
            ["status", *_target_args(context, service=False), "--json"],
            timeout=60,
        ),
        label="project status",
    )
    _parse_json(
        _run_cli(
            context,
            ["service", "status", *_target_args(context), "--json"],
            timeout=60,
        ),
        label="service status",
    )
    return "READY", {}


def _deploy(
    contract: dict[str, Any], context: Mapping[str, str], args: argparse.Namespace
) -> tuple[str, dict[str, Any]]:
    _validate_release_inputs(args)
    image_ref = _validate_image_ref(contract, args.image_ref)

    _scale(context, 0)
    for name, value in _fixed_runtime_variables(args).items():
        _set_variable(context, name, value)
    for name in contract["runtime_secret_envs"]:
        _set_variable(context, name, str(os.environ.get(name) or ""))
    for name in contract["runtime_optional_envs"]:
        value = str(os.environ.get(name) or "").strip()
        if value:
            _set_variable(context, name, value)

    before = {_deployment_id(row) for row in _deployment_rows(context)}
    _run_cli(
        context,
        [
            "service",
            "source",
            "connect",
            "--image",
            image_ref,
            *_target_args(context),
            "--json",
        ],
        timeout=120,
    )
    deadline = time.monotonic() + 900
    created: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        created = [
            row for row in _deployment_rows(context) if _deployment_id(row) not in before
        ]
        if len(created) > 1:
            raise RuntimeError("Railway exposed multiple new Worker deployments")
        if created:
            deployment_state = str(
                created[0].get("status") or created[0].get("state") or ""
            ).strip().upper()
            if deployment_state == "SUCCESS":
                break
            if deployment_state in {"FAILED", "CRASHED", "REMOVED"}:
                raise RuntimeError(
                    f"Railway Worker deployment ended in {deployment_state}"
                )
        time.sleep(5)
    if len(created) != 1 or str(
        created[0].get("status") or created[0].get("state") or ""
    ).upper() != "SUCCESS":
        raise RuntimeError("Railway Worker deployment did not become successful")
    worker_deployment_id = _required_coordinate(
        _deployment_id(created[0]), "Worker deployment"
    )
    return "STAGED", {
        "source_sha": args.source_sha,
        "runtime_bundle_id": args.runtime_bundle_id,
        "api_deployment_id": args.api_deployment_id,
        "worker_deployment_id": worker_deployment_id,
        "worker_image_digest": image_ref.rsplit("@", 1)[1],
    }


def _find_expected_deployment(
    context: Mapping[str, str], expected_deployment_id: str
) -> dict[str, Any]:
    expected = _required_coordinate(expected_deployment_id, "Worker deployment")
    matches = [row for row in _deployment_rows(context) if _deployment_id(row) == expected]
    if len(matches) != 1:
        raise RuntimeError("expected Railway Worker deployment is not uniquely present")
    return matches[0]


def _start(context: Mapping[str, str], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    _validate_release_inputs(args)
    _find_expected_deployment(context, args.expected_deployment_id)
    _scale(context, 1)
    return "RUNNING", {
        "source_sha": args.source_sha,
        "runtime_bundle_id": args.runtime_bundle_id,
        "api_deployment_id": args.api_deployment_id,
        "worker_deployment_id": args.expected_deployment_id,
    }


def _stop(context: Mapping[str, str], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.expected_deployment_id:
        _find_expected_deployment(context, args.expected_deployment_id)
    _scale(context, 0)
    coordinates = {}
    if args.expected_deployment_id:
        coordinates["worker_deployment_id"] = args.expected_deployment_id
    release_values = (
        args.source_sha,
        args.runtime_bundle_id,
        args.api_deployment_id,
        args.expected_image_digest,
    )
    if any(release_values):
        if not all(release_values):
            raise ValueError("Worker stop release binding must be complete")
        if not _SOURCE_SHA.fullmatch(args.source_sha):
            raise ValueError("Worker stop source SHA is invalid")
        if not _RUNTIME_ID.fullmatch(args.runtime_bundle_id):
            raise ValueError("Worker stop runtime bundle ID is invalid")
        _required_coordinate(args.api_deployment_id, "API deployment")
        digest = str(args.expected_image_digest).strip().lower()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Worker stop image digest is invalid")
        coordinates.update(
            {
                "source_sha": args.source_sha,
                "runtime_bundle_id": args.runtime_bundle_id,
                "api_deployment_id": args.api_deployment_id,
                "worker_image_digest": digest,
            }
        )
    return "STOPPED", coordinates


def _status(context: Mapping[str, str], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    _validate_release_inputs(args)
    digest = str(args.expected_image_digest or "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("expected Worker image digest is invalid")
    args.expected_image_digest = digest
    row = _find_expected_deployment(context, args.expected_deployment_id)
    state = str(row.get("status") or row.get("state") or "").strip().upper()
    if state != "SUCCESS":
        raise RuntimeError(f"Railway Worker deployment is not successful: {state or 'UNKNOWN'}")
    readiness_sha256, version_sha256 = _wait_for_api_runtime(args)
    return "RUNNING", {
        "source_sha": args.source_sha,
        "runtime_bundle_id": args.runtime_bundle_id,
        "api_deployment_id": args.api_deployment_id,
        "worker_deployment_id": args.expected_deployment_id,
        "worker_image_digest": digest,
        "api_readiness_sha256": readiness_sha256,
        "api_version_sha256": version_sha256,
    }


def _wait_for_api_runtime(args: argparse.Namespace) -> tuple[str, str]:
    parsed = urlsplit(str(args.api_base_url or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Worker status requires one exact HTTPS API origin")
    origin = f"https://{parsed.netloc}"
    headers = {"Accept": "application/json"}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
    deadline = time.monotonic() + args.api_ready_timeout_seconds
    last_error = "not checked"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(path: str) -> tuple[dict[str, Any], bytes]:
        request = urllib.request.Request(f"{origin}{path}", headers=headers)
        with opener.open(request, timeout=15) as response:
            if response.status != 200 or response.geturl() != f"{origin}{path}":
                raise RuntimeError("API runtime endpoint redirected or failed")
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise RuntimeError("API runtime endpoint response is oversized")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("API runtime endpoint response is not an object")
        return payload, raw

    while time.monotonic() < deadline:
        try:
            readiness, readiness_raw = fetch("/health/ready")
            version, version_raw = fetch("/api/v1/version")
            if not isinstance(readiness, dict) or readiness.get("ready") is not True:
                raise RuntimeError("API readiness has not observed the exact Worker heartbeat")
            expected = {
                "schema": "vowpic.runtime-bundle-report.v1",
                "source_sha": args.source_sha,
                "runtime_bundle_id": args.runtime_bundle_id,
                "deployment_id": args.api_deployment_id,
                "release_role": "COMMERCIAL_7A",
                "runtime_environment": "production",
                "worker_image_digest": args.expected_image_digest,
            }
            if not isinstance(version, dict) or any(
                version.get(field) != value for field, value in expected.items()
            ):
                raise RuntimeError("API runtime version does not match the Worker release")
            return (
                _sha256_bytes(readiness_raw),
                _sha256_bytes(version_raw),
            )
        except (
            urllib.error.URLError,
            OSError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            last_error = type(exc).__name__
            time.sleep(5)
    raise RuntimeError(f"API runtime did not become Worker-ready: {last_error}")


def _logs(context: Mapping[str, str], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    _find_expected_deployment(context, args.expected_deployment_id)
    raw = _run_cli(
        context,
        [
            "logs",
            args.expected_deployment_id,
            *_target_args(context),
            "--lines",
            str(args.lines),
            "--json",
        ],
        timeout=120,
    ).encode("utf-8")
    return "CAPTURED", {
        "worker_deployment_id": args.expected_deployment_id,
        "log_sha256": _sha256_bytes(raw),
        "log_bytes": len(raw),
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_bytes(payload).decode("utf-8"))
        handle.write("\n")


def _write_job_env(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    deployment_id = str(report["coordinates"].get("worker_deployment_id") or "")
    if not deployment_id:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"WORKER_DEPLOYMENT_ID={deployment_id}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        child = subparsers.add_parser(name)
        child.add_argument("--contract", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        child.add_argument("--job-env", type=Path)
        return child

    common("preflight")
    deploy = common("deploy")
    for child in (deploy,):
        child.add_argument("--source-sha", required=True)
        child.add_argument("--runtime-bundle-id", required=True)
        child.add_argument("--api-deployment-id", required=True)
        child.add_argument("--image-ref", required=True)
    start = common("start")
    start.add_argument("--source-sha", required=True)
    start.add_argument("--runtime-bundle-id", required=True)
    start.add_argument("--api-deployment-id", required=True)
    start.add_argument("--expected-deployment-id", required=True)
    stop = common("stop")
    stop.add_argument("--expected-deployment-id")
    stop.add_argument("--source-sha")
    stop.add_argument("--runtime-bundle-id")
    stop.add_argument("--api-deployment-id")
    stop.add_argument("--expected-image-digest")
    status = common("status")
    status.add_argument("--source-sha", required=True)
    status.add_argument("--runtime-bundle-id", required=True)
    status.add_argument("--api-deployment-id", required=True)
    status.add_argument("--expected-deployment-id", required=True)
    status.add_argument("--expected-image-digest", required=True)
    status.add_argument("--api-base-url", required=True)
    status.add_argument(
        "--api-ready-timeout-seconds", type=int, default=300, choices=range(10, 601)
    )
    logs = common("logs")
    logs.add_argument("--expected-deployment-id", required=True)
    logs.add_argument("--lines", type=int, default=100, choices=range(1, 501))
    return parser


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    contract, contract_sha256 = load_contract(args.contract)
    context = runtime_context(contract, os.environ)
    version = _run_cli(context, ["--version"], timeout=30).strip()
    if version != f"railway {contract['cli']['version']}":
        raise ValueError("Railway CLI version mismatch")

    if args.action == "preflight":
        state, action_coordinates = _preflight(context)
    elif args.action == "deploy":
        state, action_coordinates = _deploy(contract, context, args)
    elif args.action == "start":
        state, action_coordinates = _start(context, args)
    elif args.action == "stop":
        state, action_coordinates = _stop(context, args)
    elif args.action == "status":
        state, action_coordinates = _status(context, args)
    else:
        state, action_coordinates = _logs(context, args)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "passed": True,
        "action": args.action,
        "provider": "railway",
        "contract_sha256": contract_sha256,
        "state": state,
        "coordinates": {**_base_coordinates(context), **action_coordinates},
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    signing_key = os.environ.get("WORKER_HOST_EVIDENCE_SIGNING_KEY", "").encode("utf-8")
    if len(signing_key) < 32:
        raise WorkerHostNotRun("Worker host evidence signing key is unavailable")
    signature = hmac.new(signing_key, _canonical_bytes(unsigned), hashlib.sha256).hexdigest()
    report = {**unsigned, "signature": f"hmac-sha256:{signature}"}
    _write_create_once(args.output, report)
    _write_job_env(args.job_env, report)
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except WorkerHostNotRun as exc:
        print(f"NOT_RUN: {exc}", file=sys.stderr)
        return NOT_RUN_EXIT
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
