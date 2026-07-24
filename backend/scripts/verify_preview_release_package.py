#!/usr/bin/env python3
"""Build or verify the exact PR plus Preview release-evidence package."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME = re.compile(r"^rtb_[0-9a-f]{64}$")
DEPLOYMENT = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
UUID_CANONICAL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
PREVIEW_SCOPES = {
    "pr",
    "preview_identity",
    "preview_commercial",
    "stage5_composite",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _contract(path: Path) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    payload = _load(path)
    if payload.get("schema") != "vowpic.gates.v1":
        raise ValueError("gate contract schema is invalid")
    cases = {
        str(item["id"]): item
        for item in payload.get("cases", [])
        if isinstance(item, dict) and item.get("id")
    }
    return payload, hashlib.sha256(path.read_bytes()).hexdigest(), cases


def required_case_ids(contract: dict[str, Any], cases: dict[str, dict[str, Any]]) -> set[str]:
    return {
        case_id
        for case_id in contract["profiles"]["release"]
        if cases[case_id].get("runtime_scope") in PREVIEW_SCOPES
    }


def _evidence_rows(
    root: Path,
    *,
    required: set[str],
    source_sha: str,
    gate_sha256: str,
    cases: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bindings: dict[str, str] = {}
    commercial_binding: dict[str, Any] | None = None
    for case_id in sorted(required):
        matches = list(root.rglob(f"{case_id}.json"))
        if len(matches) != 1:
            raise ValueError(f"evidence root must contain exactly one {case_id}.json")
        row = _load(matches[0])
        runtime = str(row.get("runtime_bundle_id") or "")
        if (
            row.get("schema") != "vowpic.gate-evidence.v1"
            or row.get("case_id") != case_id
            or row.get("status") != "PASS"
            or row.get("execution") != "completed"
            or row.get("source_sha") != source_sha
            or row.get("gate_contract_sha256") != gate_sha256
            or type(row.get("test_count")) is not int
            or int(row["test_count"]) < 1
            or not RUNTIME.fullmatch(runtime)
        ):
            raise ValueError(f"{case_id} is not one exact current PASS")
        scope = str(cases[case_id]["runtime_scope"])
        prior = bindings.setdefault(scope, runtime)
        if prior != runtime:
            raise ValueError(f"{scope} evidence spans multiple runtime bundles")
        if scope == "preview_commercial":
            candidate = {
                "activation_id": str(row.get("activation_id") or ""),
                "workflow_run_id": str(row.get("workflow_run_id") or ""),
                "workflow_attempt": row.get("workflow_attempt"),
                "runtime_bundle_id": runtime,
                "api_deployment_id": str(row.get("api_deployment_id") or ""),
                "manifest_sha256": str(row.get("manifest_sha256") or ""),
            }
            if (
                not UUID_CANONICAL.fullmatch(candidate["activation_id"])
                or not candidate["workflow_run_id"].isdigit()
                or type(candidate["workflow_attempt"]) is not int
                or candidate["workflow_attempt"] < 1
                or not DEPLOYMENT.fullmatch(candidate["api_deployment_id"])
                or not SHA64.fullmatch(candidate["manifest_sha256"])
            ):
                raise ValueError(
                    "Preview Commercial evidence binding is incomplete or invalid"
                )
            if commercial_binding is None:
                commercial_binding = candidate
            elif commercial_binding != candidate:
                raise ValueError(
                    "Preview Commercial evidence spans multiple activation coordinates"
                )
        rows.append(row)
    if set(bindings) != PREVIEW_SCOPES:
        raise ValueError("Preview release runtime bindings are incomplete")
    if commercial_binding is None:
        raise ValueError("Preview Commercial activation binding is absent")
    return rows, bindings, commercial_binding


def build_package(
    *,
    evidence_root: Path,
    gate_contract_path: Path,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not SHA40.fullmatch(source_sha):
        raise ValueError("Preview package source SHA is invalid")
    if not workflow_run_id.isdigit() or workflow_attempt < 1:
        raise ValueError("Preview package workflow coordinate is invalid")
    contract, gate_hash, cases = _contract(gate_contract_path)
    required = required_case_ids(contract, cases)
    rows, bindings, commercial_binding = _evidence_rows(
        evidence_root,
        required=required,
        source_sha=source_sha,
        gate_sha256=gate_hash,
        cases=cases,
    )
    if (
        commercial_binding["workflow_run_id"] != workflow_run_id
        or commercial_binding["workflow_attempt"] != workflow_attempt
    ):
        raise ValueError(
            "Preview Commercial activation does not belong to this workflow attempt"
        )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Preview package timestamp must be timezone-aware")
    evidence = [
        {
            "case_id": row["case_id"],
            "sha256": hashlib.sha256(_canonical(row)).hexdigest(),
        }
        for row in rows
    ]
    unsigned = {
        "schema": "vowpic.preview-release-aggregate.v1",
        "passed": True,
        "source_sha": source_sha,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "gate_contract_sha256": gate_hash,
        "runtime_bindings": dict(sorted(bindings.items())),
        "preview_commercial_binding": commercial_binding,
        "case_ids": sorted(required),
        "evidence": evidence,
        "produced_at": current.astimezone(timezone.utc).isoformat(),
    }
    return {
        **unsigned,
        "aggregate_sha256": hashlib.sha256(_canonical(unsigned)).hexdigest(),
    }


def verify_package(
    package: dict[str, Any],
    *,
    evidence_root: Path,
    gate_contract_path: Path,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> dict[str, Any]:
    contract, gate_hash, cases = _contract(gate_contract_path)
    required = required_case_ids(contract, cases)
    expected_keys = {
        "schema",
        "passed",
        "source_sha",
        "workflow_run_id",
        "workflow_attempt",
        "gate_contract_sha256",
        "runtime_bindings",
        "preview_commercial_binding",
        "case_ids",
        "evidence",
        "produced_at",
        "aggregate_sha256",
    }
    if set(package) != expected_keys:
        raise ValueError("Preview package schema is not exact")
    if (
        package["schema"] != "vowpic.preview-release-aggregate.v1"
        or package["passed"] is not True
        or package["source_sha"] != source_sha
        or str(package["workflow_run_id"]) != workflow_run_id
        or int(package["workflow_attempt"]) != workflow_attempt
        or package["gate_contract_sha256"] != gate_hash
        or set(package["case_ids"]) != required
        or set(package["runtime_bindings"]) != PREVIEW_SCOPES
        or any(
            not RUNTIME.fullmatch(str(value))
            for value in package["runtime_bindings"].values()
        )
    ):
        raise ValueError("Preview package coordinates or cases do not match")
    binding = package.get("preview_commercial_binding")
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "activation_id",
            "workflow_run_id",
            "workflow_attempt",
            "runtime_bundle_id",
            "api_deployment_id",
            "manifest_sha256",
        }
        or binding.get("workflow_run_id") != workflow_run_id
        or binding.get("workflow_attempt") != workflow_attempt
        or binding.get("runtime_bundle_id")
        != package["runtime_bindings"]["preview_commercial"]
        or not UUID_CANONICAL.fullmatch(str(binding.get("activation_id") or ""))
        or not DEPLOYMENT.fullmatch(str(binding.get("api_deployment_id") or ""))
        or not SHA64.fullmatch(str(binding.get("manifest_sha256") or ""))
    ):
        raise ValueError("Preview Commercial package binding is invalid")
    evidence = package.get("evidence")
    if (
        not isinstance(evidence, list)
        or len(evidence) != len(required)
        or {item.get("case_id") for item in evidence if isinstance(item, dict)}
        != required
        or any(
            not isinstance(item, dict)
            or set(item) != {"case_id", "sha256"}
            or not SHA64.fullmatch(str(item.get("sha256") or ""))
            for item in evidence
        )
    ):
        raise ValueError("Preview package evidence index is invalid")
    unsigned = {key: value for key, value in package.items() if key != "aggregate_sha256"}
    wanted = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if package["aggregate_sha256"] != wanted:
        raise ValueError("Preview package aggregate hash mismatch")
    produced_at = datetime.fromisoformat(str(package["produced_at"]))
    if produced_at.tzinfo is None or produced_at.utcoffset() is None:
        raise ValueError("Preview package timestamp is not timezone-aware")

    rows, bindings, actual_commercial_binding = _evidence_rows(
        evidence_root,
        required=required,
        source_sha=source_sha,
        gate_sha256=gate_hash,
        cases=cases,
    )
    actual_hashes = {
        str(row["case_id"]): hashlib.sha256(_canonical(row)).hexdigest()
        for row in rows
    }
    indexed_hashes = {
        str(item["case_id"]): str(item["sha256"])
        for item in evidence
    }
    if (
        actual_hashes != indexed_hashes
        or dict(sorted(bindings.items())) != package["runtime_bindings"]
        or actual_commercial_binding != binding
    ):
        raise ValueError("Preview package downloaded evidence hash drift")
    return {
        "schema": "vowpic.preview-release-verification.v1",
        "passed": True,
        "source_sha": source_sha,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "preview_commercial_binding": binding,
        "aggregate_sha256": wanted,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--evidence-root", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--package", required=True)
    verify.add_argument("--evidence-root", required=True)
    for command in (build, verify):
        command.add_argument("--gate-contract", default="release/gates.json")
        command.add_argument("--source-sha", required=True)
        command.add_argument("--workflow-run-id", required=True)
        command.add_argument("--workflow-attempt", required=True, type=int)
        command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            result = build_package(
                evidence_root=Path(args.evidence_root),
                gate_contract_path=Path(args.gate_contract),
                source_sha=args.source_sha.strip().lower(),
                workflow_run_id=args.workflow_run_id.strip(),
                workflow_attempt=args.workflow_attempt,
            )
        else:
            result = verify_package(
                _load(Path(args.package)),
                evidence_root=Path(args.evidence_root),
                gate_contract_path=Path(args.gate_contract),
                source_sha=args.source_sha.strip().lower(),
                workflow_run_id=args.workflow_run_id.strip(),
                workflow_attempt=args.workflow_attempt,
            )
        _write(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
