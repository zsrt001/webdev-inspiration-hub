"""Aggregate exact release evidence against the committed fail-closed registry."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterable, Sequence


STATUSES = ("PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE")
EXECUTIONS = frozenset({"completed", "timed_out", "cancelled", "skipped"})
EVIDENCE_PATH = re.compile(
    r"^artifacts/release/[0-9a-f]{40}/[1-9][0-9]*-[1-9][0-9]*/"
    r"[^/]+/(?:01-ci|02-integration|03-production|04-observation|05-migration|"
    r"06-privacy|07-quality|08-cleanup)/[A-Za-z0-9._-]+\.json$"
)
RUNTIME_SCOPE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
RUNTIME_BUNDLE_ID = re.compile(r"^rtb_[0-9a-f]{64}$")
SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:access_token|refresh_token|csrf|secret|password|cookie|authorization|"
    r"private_key|email|object_url|storage_url|payment_credential)(?:$|_)",
    re.IGNORECASE,
)
SENSITIVE_VALUE = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"https?://[^\s]*(?:storage|blob|object)[^\s]*", re.IGNORECASE),
)


class GateContractError(ValueError):
    """The registry or its evidence is malformed or contradictory."""


@dataclass(frozen=True)
class AggregateResult:
    profile: str
    status: str
    case_statuses: dict[str, str]
    missing_case_ids: list[str]
    unexpected_case_ids: list[str]
    duplicate_case_ids: list[str]
    invalid_case_ids: list[str]
    stage5_foundation_ready: bool
    release_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateContractError("evidence produced_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateContractError("evidence produced_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_evidence_path(value: str) -> None:
    path = str(value or "").replace("\\", "/")
    if ".." in PurePosixPath(path).parts or "/latest/" in f"/{path.lower()}/":
        raise ValueError("release evidence path is mutable or traverses directories")
    if not EVIDENCE_PATH.fullmatch(path):
        raise ValueError("release evidence path is not content scoped")


def validate_secret_free(payload: object) -> None:
    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                name = str(key)
                if SENSITIVE_KEY.search(name):
                    raise ValueError(f"sensitive evidence key: {path}{name}")
                visit(nested, f"{path}{name}.")
            return
        if isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}{index}.")
            return
        if isinstance(value, str) and any(pattern.search(value) for pattern in SENSITIVE_VALUE):
            raise ValueError(f"sensitive evidence value: {path.rstrip('.')}")

    visit(payload, "")


def _validated_contract(contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    if contract.get("schema") != "vowpic.gates.v1":
        raise GateContractError("gate contract schema is unsupported")
    if tuple(contract.get("statuses", ())) != STATUSES:
        raise GateContractError("gate contract status set is not exact")
    cases = contract.get("cases")
    profiles = contract.get("profiles")
    if not isinstance(cases, list) or not isinstance(profiles, dict):
        raise GateContractError("gate contract cases or profiles are invalid")
    by_id: dict[str, dict[str, Any]] = {}
    required_fields = {
        "id",
        "layer",
        "runtime_scope",
        "profiles",
        "mandatory",
        "timeout_seconds",
        "freshness_seconds",
        "report_schema",
        "na_eligible",
        "capability",
    }
    for case in cases:
        if not isinstance(case, dict) or set(case) != required_fields:
            raise GateContractError("gate case shape is not exact")
        case_id = str(case["id"])
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", case_id) or case_id in by_id:
            raise GateContractError("gate case ID is invalid or duplicated")
        if not isinstance(case["profiles"], list) or not case["profiles"]:
            raise GateContractError("gate case profiles are invalid")
        if not RUNTIME_SCOPE.fullmatch(str(case["runtime_scope"])):
            raise GateContractError("gate case runtime scope is invalid")
        if not isinstance(case["mandatory"], bool) or not isinstance(case["na_eligible"], bool):
            raise GateContractError("gate case boolean fields are invalid")
        if int(case["timeout_seconds"]) <= 0 or int(case["freshness_seconds"]) <= 0:
            raise GateContractError("gate case time bounds are invalid")
        by_id[case_id] = case
    normalized_profiles: dict[str, list[str]] = {}
    for profile, raw_ids in profiles.items():
        if not isinstance(raw_ids, list) or len(raw_ids) != len(set(raw_ids)):
            raise GateContractError("gate profile case IDs are invalid")
        ids = [str(case_id) for case_id in raw_ids]
        if any(case_id not in by_id for case_id in ids):
            raise GateContractError("gate profile references an unknown case")
        declared = {case_id for case_id, case in by_id.items() if profile in case["profiles"]}
        if set(ids) != declared:
            raise GateContractError("gate profile silently omits or adds a declared case")
        normalized_profiles[str(profile)] = ids
    for case in by_id.values():
        if any(profile not in normalized_profiles for profile in case["profiles"]):
            raise GateContractError("gate case references an unknown profile")
    return by_id, normalized_profiles


def aggregate_one(
    case: dict[str, Any],
    evidence: dict[str, Any],
    *,
    enabled_capabilities: set[str],
) -> str:
    status = str(evidence.get("status", ""))
    execution = str(evidence.get("execution", ""))
    if status not in STATUSES or execution not in EXECUTIONS:
        return "FAIL"
    if execution != "completed":
        return "FAIL"
    if status == "PASS":
        return "PASS" if int(evidence.get("test_count", 0)) > 0 else "FAIL"
    if status == "NOT_APPLICABLE":
        capability = str(case.get("capability") or "")
        if not bool(case.get("na_eligible")):
            return "FAIL"
        if capability and capability in enabled_capabilities:
            return "FAIL"
        if evidence.get("capability_enabled") is True:
            return "FAIL"
        return "PASS"
    return "FAIL"


def _case_is_valid(
    case: dict[str, Any],
    evidence: dict[str, Any],
    *,
    expected_source_sha: str,
    expected_runtime_bindings: dict[str, str],
    expected_contract_sha256: str,
    now: datetime,
    enabled_capabilities: set[str],
) -> bool:
    try:
        validate_secret_free(evidence)
        if evidence.get("schema") != case["report_schema"]:
            return False
        if evidence.get("source_sha") != expected_source_sha:
            return False
        expected_runtime_bundle_id = expected_runtime_bindings.get(
            str(case["runtime_scope"])
        )
        if (
            expected_runtime_bundle_id is None
            or evidence.get("runtime_bundle_id") != expected_runtime_bundle_id
        ):
            return False
        if evidence.get("gate_contract_sha256") != expected_contract_sha256:
            return False
        produced = _parse_time(evidence.get("produced_at"))
        age = (now.astimezone(timezone.utc) - produced).total_seconds()
        if age < -30 or age > int(case["freshness_seconds"]):
            return False
        return aggregate_one(
            case,
            evidence,
            enabled_capabilities=enabled_capabilities,
        ) == "PASS"
    except (GateContractError, TypeError, ValueError):
        return False


def _evaluate_profile(
    profile: str,
    *,
    by_id: dict[str, dict[str, Any]],
    profiles: dict[str, list[str]],
    evidence_by_id: dict[str, list[dict[str, Any]]],
    expected_source_sha: str,
    expected_runtime_bindings: dict[str, str],
    expected_contract_sha256: str,
    now: datetime,
    enabled_capabilities: set[str],
) -> tuple[bool, dict[str, str], list[str], list[str], list[str], list[str]]:
    expected_ids = profiles.get(profile)
    if expected_ids is None:
        raise GateContractError(f"unknown gate profile: {profile}")
    expected = set(expected_ids)
    supplied = set(evidence_by_id)
    missing = sorted(expected - supplied)
    unexpected = sorted(supplied - expected)
    duplicates = sorted(case_id for case_id, rows in evidence_by_id.items() if len(rows) > 1)
    invalid: list[str] = []
    statuses: dict[str, str] = {}
    for case_id in expected_ids:
        rows = evidence_by_id.get(case_id, [])
        if len(rows) != 1:
            statuses[case_id] = "FAIL"
            continue
        valid = _case_is_valid(
            by_id[case_id],
            rows[0],
            expected_source_sha=expected_source_sha,
            expected_runtime_bindings=expected_runtime_bindings,
            expected_contract_sha256=expected_contract_sha256,
            now=now,
            enabled_capabilities=enabled_capabilities,
        )
        statuses[case_id] = "PASS" if valid else "FAIL"
        if not valid:
            invalid.append(case_id)
    passed = not (missing or unexpected or duplicates or invalid) and all(
        status == "PASS" for status in statuses.values()
    )
    return passed, statuses, missing, unexpected, duplicates, sorted(invalid)


def aggregate(
    contract: dict[str, Any],
    evidence: Sequence[dict[str, Any]],
    *,
    profile: str,
    expected_source_sha: str,
    expected_runtime_bundle_id: str | None = None,
    expected_runtime_bindings: dict[str, str] | None = None,
    expected_contract_sha256: str,
    now: datetime | None = None,
    enabled_capabilities: set[str] | None = None,
) -> AggregateResult:
    by_id, profiles = _validated_contract(contract)
    profile_ids = profiles.get(profile)
    if profile_ids is None:
        raise GateContractError(f"unknown gate profile: {profile}")
    required_scopes = {str(by_id[case_id]["runtime_scope"]) for case_id in profile_ids}
    if expected_runtime_bindings is not None and expected_runtime_bundle_id is not None:
        raise GateContractError("provide either one runtime bundle or runtime scope bindings")
    if expected_runtime_bindings is None:
        runtime = str(expected_runtime_bundle_id or "").strip().lower()
        if not RUNTIME_BUNDLE_ID.fullmatch(runtime):
            raise GateContractError("expected runtime bundle ID is invalid")
        runtime_bindings = {scope: runtime for scope in required_scopes}
    else:
        runtime_bindings = {
            str(scope): str(runtime).strip().lower()
            for scope, runtime in expected_runtime_bindings.items()
        }
        if (
            set(runtime_bindings) != required_scopes
            or any(not RUNTIME_SCOPE.fullmatch(scope) for scope in runtime_bindings)
            or any(not RUNTIME_BUNDLE_ID.fullmatch(runtime) for runtime in runtime_bindings.values())
        ):
            raise GateContractError("runtime scope bindings must exactly match the selected profile")
    evidence_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        case_id = str(row.get("case_id", ""))
        evidence_by_id.setdefault(case_id, []).append(row)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    capabilities = enabled_capabilities or set()
    passed, statuses, missing, unexpected, duplicates, invalid = _evaluate_profile(
        profile,
        by_id=by_id,
        profiles=profiles,
        evidence_by_id=evidence_by_id,
        expected_source_sha=expected_source_sha,
        expected_runtime_bindings=runtime_bindings,
        expected_contract_sha256=expected_contract_sha256,
        now=current,
        enabled_capabilities=capabilities,
    )

    def readiness(readiness_profile: str) -> bool:
        if readiness_profile not in profiles:
            return False
        readiness_ids = set(profiles[readiness_profile])
        readiness_evidence = {
            case_id: rows
            for case_id, rows in evidence_by_id.items()
            if case_id in readiness_ids
        }
        ready, *_ = _evaluate_profile(
            readiness_profile,
            by_id=by_id,
            profiles=profiles,
            evidence_by_id=readiness_evidence,
            expected_source_sha=expected_source_sha,
            expected_runtime_bindings=runtime_bindings,
            expected_contract_sha256=expected_contract_sha256,
            now=current,
            enabled_capabilities=capabilities,
        )
        return ready

    return AggregateResult(
        profile=profile,
        status="PASS" if passed else "FAIL",
        case_statuses=statuses,
        missing_case_ids=missing,
        unexpected_case_ids=unexpected,
        duplicate_case_ids=duplicates,
        invalid_case_ids=invalid,
        stage5_foundation_ready=readiness("stage5_foundation"),
        release_ready=readiness("release"),
    )


def evaluate_change_impact(contract: dict[str, Any], paths: Iterable[str | None]) -> str:
    full_patterns = [str(pattern) for pattern in contract.get("full_quality_globs", [])]
    canary_patterns = [str(pattern) for pattern in contract.get("canary_only_globs", [])]
    normalized: list[str] = []
    for raw_path in paths:
        if raw_path is None:
            return "FULL_QUALITY"
        path = str(raw_path).strip().replace("\\", "/")
        if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
            return "FULL_QUALITY"
        normalized.append(path)
    if not normalized:
        return "FULL_QUALITY"
    if any(fnmatch.fnmatchcase(path, pattern) for path in normalized for pattern in full_patterns):
        return "FULL_QUALITY"
    if all(any(fnmatch.fnmatchcase(path, pattern) for pattern in canary_patterns) for path in normalized):
        return "CANARY_ONLY"
    return str(contract.get("unknown_or_unparseable", "FULL_QUALITY"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--expected-source-sha", required=True)
    runtime_group = parser.add_mutually_exclusive_group(required=True)
    runtime_group.add_argument("--expected-runtime-bundle-id")
    runtime_group.add_argument("--expected-runtime-binding", action="append")
    parser.add_argument("--enabled-capability", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    contract_path = Path(args.contract)
    output_path = Path(args.output)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()
        evidence: list[dict[str, Any]] = []
        for raw_path in args.evidence:
            validate_evidence_path(raw_path)
            evidence.append(json.loads(Path(raw_path).read_text(encoding="utf-8")))
        runtime_bindings = None
        if args.expected_runtime_binding is not None:
            runtime_bindings = {}
            for raw in args.expected_runtime_binding:
                scope, separator, runtime = str(raw).partition("=")
                if not separator or scope in runtime_bindings:
                    raise GateContractError("runtime scope bindings are malformed or duplicated")
                runtime_bindings[scope] = runtime
        result = aggregate(
            contract,
            evidence,
            profile=args.profile,
            expected_source_sha=args.expected_source_sha,
            expected_runtime_bundle_id=args.expected_runtime_bundle_id,
            expected_runtime_bindings=runtime_bindings,
            expected_contract_sha256=contract_hash,
            enabled_capabilities=set(args.enabled_capability),
        )
        _write_json(output_path, result.to_dict())
        return 0 if result.status == "PASS" else 1
    except (GateContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        _write_json(output_path, {"profile": args.profile, "status": "FAIL", "error": str(exc)})
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
