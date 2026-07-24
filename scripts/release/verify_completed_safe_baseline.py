#!/usr/bin/env python3
"""Fail-closed verification for an immutable completed safe baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.github_artifact_evidence import parse_reference  # noqa: E402


SCHEMA_VERSION = "vowpic.safe-baseline-completed.v1"
TARGET_SCHEMA = "20260712_0014"
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
RUNTIME_BUNDLE = re.compile(r"rtb_[0-9a-f]{64}")
DEPLOYMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,255}")


class CompletedSafeBaselineError(RuntimeError):
    pass


def _sha40(value: Any, *, label: str) -> str:
    clean = str(value or "").strip()
    if SHA40.fullmatch(clean) is None:
        raise CompletedSafeBaselineError(
            f"{label} must be one lowercase 40-character commit SHA"
        )
    return clean


def _sha256(value: Any, *, label: str) -> str:
    clean = str(value or "").strip()
    if SHA256.fullmatch(clean) is None:
        raise CompletedSafeBaselineError(
            f"{label} must be one lowercase 64-character SHA-256"
        )
    return clean


def _positive_decimal(value: Any, *, label: str, digits: int = 20) -> str:
    clean = str(value or "").strip()
    if re.fullmatch(rf"[1-9][0-9]{{0,{digits - 1}}}", clean) is None:
        raise CompletedSafeBaselineError(f"{label} must be a positive decimal value")
    return clean


def _repository(value: Any) -> str:
    clean = str(value or "").strip()
    parts = clean.split("/")
    if len(parts) != 2 or any(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) is None for part in parts
    ):
        raise CompletedSafeBaselineError(
            "expected repository must be an owner/name pair"
        )
    return clean


def _deployment_url(value: Any) -> str:
    clean = str(value or "").strip().lower()
    parsed = urlsplit(clean)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(".vercel.app")
    ):
        raise CompletedSafeBaselineError(
            "completed API deployment URL must be one canonical vercel.app origin"
        )
    return f"https://{parsed.hostname}"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompletedSafeBaselineError(
            "safe-baseline preflight is unavailable or invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise CompletedSafeBaselineError(
            "safe-baseline preflight must be one JSON object"
        )
    return payload


def verify_completed_state(
    preflight: dict[str, Any],
    *,
    expected_source_sha: str,
    expected_runner_sha: str,
    expected_repository: str,
) -> tuple[dict[str, Any], str, str]:
    """Return a sanitized report plus terminal evidence outputs."""
    source_sha = _sha40(expected_source_sha, label="expected source SHA")
    runner_sha = _sha40(expected_runner_sha, label="expected runner SHA")
    repository = _repository(expected_repository)
    if _sha40(preflight.get("source_sha"), label="preflight source SHA") != source_sha:
        raise CompletedSafeBaselineError(
            "safe-baseline preflight source SHA does not match the request"
        )
    if _sha40(preflight.get("runner_sha"), label="preflight runner SHA") != runner_sha:
        raise CompletedSafeBaselineError(
            "safe-baseline preflight runner SHA does not match the request"
        )

    state = str(preflight.get("state") or "").strip()
    if not state:
        raise CompletedSafeBaselineError("safe-baseline preflight state is missing")
    base_report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "already_completed": state == "ALREADY_COMPLETED",
        "state": state,
        "requested_source_sha": source_sha,
        "reviewed_runner_sha": runner_sha,
        "repository": repository,
    }
    if state != "ALREADY_COMPLETED":
        return base_report, "", ""

    if str(preflight.get("schema_revision") or "").strip() != TARGET_SCHEMA:
        raise CompletedSafeBaselineError(
            "completed safe baseline is not pinned to the target schema"
        )
    if str(preflight.get("activation_phase") or "").strip() != "COMPLETED":
        raise CompletedSafeBaselineError(
            "completed safe-baseline preflight has a nonterminal activation phase"
        )
    activation = preflight.get("activation")
    if not isinstance(activation, dict):
        raise CompletedSafeBaselineError(
            "completed safe-baseline preflight omitted its activation"
        )
    if str(activation.get("phase") or "").strip() != "COMPLETED":
        raise CompletedSafeBaselineError(
            "completed safe-baseline activation phase does not match"
        )
    if _sha40(activation.get("source_sha"), label="activation source SHA") != source_sha:
        raise CompletedSafeBaselineError(
            "completed safe-baseline source SHA does not match the requested runtime"
        )

    runtime_bundle_id = str(activation.get("runtime_bundle_id") or "").strip()
    if RUNTIME_BUNDLE.fullmatch(runtime_bundle_id) is None:
        raise CompletedSafeBaselineError(
            "completed runtime bundle ID is not immutable"
        )
    manifest_sha256 = _sha256(
        activation.get("manifest_sha256"),
        label="completed manifest SHA-256",
    )
    build_artifact_id = _positive_decimal(
        activation.get("build_artifact_id"),
        label="completed build artifact ID",
    )
    build_artifact_digest = str(
        activation.get("build_artifact_digest") or ""
    ).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", build_artifact_digest) is None:
        raise CompletedSafeBaselineError(
            "completed build artifact digest must be sha256:<64 lowercase hex>"
        )
    activation_run_id = _positive_decimal(
        activation.get("workflow_run_id"),
        label="completed workflow run ID",
    )
    activation_attempt = _positive_decimal(
        activation.get("workflow_attempt"),
        label="completed workflow attempt",
        digits=10,
    )
    activation_version = _positive_decimal(
        activation.get("version"),
        label="completed activation version",
    )
    deployment_id = str(activation.get("api_deployment_id") or "").strip()
    if DEPLOYMENT_ID.fullmatch(deployment_id) is None:
        raise CompletedSafeBaselineError(
            "completed API deployment ID is invalid"
        )
    deployment_url = _deployment_url(activation.get("api_deployment_url"))
    if str(activation.get("api_role") or "").strip() != "SAFE_BASELINE":
        raise CompletedSafeBaselineError(
            "completed API deployment role is not SAFE_BASELINE"
        )
    current_snapshot_hash = _sha256(
        activation.get("current_snapshot_hash"),
        label="completed current snapshot hash",
    )
    report_sha256 = _sha256(
        activation.get("report_sha256"),
        label="completed formal report SHA-256",
    )
    evidence_reference = str(
        activation.get("private_evidence_prefix") or ""
    ).strip()
    try:
        evidence_coordinates = parse_reference(evidence_reference)
    except ValueError as exc:
        raise CompletedSafeBaselineError(
            "completed formal evidence reference is invalid"
        ) from exc
    if evidence_coordinates["repository"] != repository:
        raise CompletedSafeBaselineError(
            "completed formal evidence belongs to a different repository"
        )
    if evidence_coordinates["run_id"] != activation_run_id:
        raise CompletedSafeBaselineError(
            "completed formal evidence belongs to a different workflow run"
        )
    target_snapshot_hash = _sha256(
        activation.get("target_snapshot_hash"),
        label="completed target snapshot hash",
    )

    base_report["activation"] = {
        "phase": "COMPLETED",
        "version": activation_version,
        "source_sha": source_sha,
        "workflow_run_id": activation_run_id,
        "workflow_attempt": activation_attempt,
        "runtime_bundle_id": runtime_bundle_id,
        "manifest_sha256": manifest_sha256,
        "build_artifact_id": build_artifact_id,
        "build_artifact_digest": build_artifact_digest,
        "api_deployment_id": deployment_id,
        "api_deployment_url": deployment_url,
        "api_role": "SAFE_BASELINE",
        "current_snapshot_hash": current_snapshot_hash,
        "target_snapshot_hash": target_snapshot_hash,
        "formal_report_sha256": report_sha256,
        "formal_evidence": evidence_coordinates,
    }
    return base_report, evidence_reference, report_sha256


def _write_github_outputs(
    path: Path,
    *,
    already_completed: bool,
    evidence_reference: str,
    report_sha256: str,
    runtime_bundle_id: str,
    deployment_id: str,
    target_snapshot_sha256: str,
    build_artifact_id: str,
    build_artifact_digest: str,
) -> None:
    values = {
        "already_completed": str(already_completed).lower(),
        "activation_evidence_reference": evidence_reference,
        "activation_report_sha256": report_sha256,
        "activation_runtime_bundle_id": runtime_bundle_id,
        "activation_deployment_id": deployment_id,
        "activation_target_snapshot_sha256": target_snapshot_sha256,
        "activation_build_artifact_id": build_artifact_id,
        "activation_build_artifact_digest": build_artifact_digest,
    }
    if any("\n" in value or "\r" in value for value in values.values()):
        raise CompletedSafeBaselineError("GitHub output value contains a newline")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for name, value in values.items():
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-runner-sha", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report, evidence_reference, report_sha256 = verify_completed_state(
            _load_object(args.preflight),
            expected_source_sha=args.expected_source_sha,
            expected_runner_sha=args.expected_runner_sha,
            expected_repository=args.expected_repository,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_github_outputs(
            args.github_output,
            already_completed=bool(report["already_completed"]),
            evidence_reference=evidence_reference,
            report_sha256=report_sha256,
            runtime_bundle_id=str(
                (report.get("activation") or {}).get("runtime_bundle_id") or ""
            ),
            deployment_id=str(
                (report.get("activation") or {}).get("api_deployment_id") or ""
            ),
            target_snapshot_sha256=str(
                (report.get("activation") or {}).get("target_snapshot_hash") or ""
            ),
            build_artifact_id=str(
                (report.get("activation") or {}).get("build_artifact_id") or ""
            ),
            build_artifact_digest=str(
                (report.get("activation") or {}).get("build_artifact_digest") or ""
            ),
        )
    except (CompletedSafeBaselineError, OSError, ValueError) as exc:
        print(f"FAIL: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
