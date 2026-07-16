#!/usr/bin/env python3
"""Bind exact PR, Preview Identity, and Preview Commercial evidence for Stage 5."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.aggregate_gates import validate_evidence_path  # noqa: E402
from scripts.release.verify_provider_grant_fetch import _git_activation_lineage  # noqa: E402


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME = re.compile(r"^rtb_[0-9a-f]{64}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
IDENTITY_CASE_IDS = frozenset(
    {
        "preview_google_session",
        "preview_private_media_owner_read",
        "preview_private_media_cross_user",
        "preview_private_media_delete",
    }
)


def _exact_sha40(value: object, *, label: str) -> str:
    clean = str(value or "").strip().lower()
    if not _SHA40.fullmatch(clean):
        raise ValueError(f"{label} is invalid")
    return clean


def _exact_sha64(value: object, *, label: str) -> str:
    clean = str(value or "").strip().lower()
    if not _SHA64.fullmatch(clean):
        raise ValueError(f"{label} is invalid")
    return clean


def _exact_runtime(value: object, *, label: str) -> str:
    clean = str(value or "").strip().lower()
    if not _RUNTIME.fullmatch(clean):
        raise ValueError(f"{label} is invalid")
    return clean


def _exact_uuid(value: object, *, label: str) -> str:
    clean = str(value or "").strip().lower()
    try:
        parsed = UUID(clean)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if str(parsed) != clean:
        raise ValueError(f"{label} is invalid")
    return clean


def _one_evidence_runtime(
    rows: Sequence[dict[str, Any]],
    *,
    expected_case_ids: set[str],
    source_sha: str,
    gate_contract_sha256: str,
    label: str,
) -> str:
    if not rows or {str(row.get("case_id") or "") for row in rows} != expected_case_ids:
        raise ValueError(f"{label} evidence case set is incomplete or unexpected")
    runtimes: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("schema") != "vowpic.gate-evidence.v1"
            or row.get("status") != "PASS"
            or row.get("execution") != "completed"
            or type(row.get("test_count")) is not int
            or int(row["test_count"]) < 1
            or row.get("source_sha") != source_sha
            or row.get("gate_contract_sha256") != gate_contract_sha256
        ):
            raise ValueError(f"{label} evidence is not an exact PASS")
        runtimes.add(_exact_runtime(row.get("runtime_bundle_id"), label=f"{label} runtime"))
    if len(runtimes) != 1:
        raise ValueError(f"{label} evidence spans more than one runtime")
    return next(iter(runtimes))


def composite_runtime_bundle_id(bindings: dict[str, str]) -> str:
    if set(bindings) != {"pr", "preview_identity", "preview_commercial"}:
        raise ValueError("Stage-5 composite inputs are incomplete")
    normalized = {
        scope: _exact_runtime(runtime, label=f"{scope} runtime")
        for scope, runtime in bindings.items()
    }
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "rtb_" + hashlib.sha256(encoded).hexdigest()


def _require_coordinates(payload: dict[str, Any], expected: dict[str, str], *, label: str) -> None:
    if not isinstance(payload, dict) or any(str(payload.get(key) or "") != value for key, value in expected.items()):
        raise ValueError(f"{label} coordinates mismatch")


def _gate_evidence(
    case_id: str,
    *,
    source_sha: str,
    runtime_bundle_id: str,
    gate_contract_sha256: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "schema": "vowpic.gate-evidence.v1",
        "case_id": case_id,
        "status": "PASS",
        "source_sha": source_sha,
        "runtime_bundle_id": runtime_bundle_id,
        "gate_contract_sha256": gate_contract_sha256,
        "produced_at": now.astimezone(timezone.utc).isoformat(),
        "execution": "completed",
        "test_count": 1,
        "capability_enabled": False,
    }


def build_stage5_materialization(
    *,
    source_sha: str,
    gate_contract_sha256: str,
    pr_evidence: Sequence[dict[str, Any]],
    expected_pr_case_ids: set[str],
    identity_evidence: Sequence[dict[str, Any]],
    identity_cleanup: dict[str, Any],
    activation: dict[str, Any],
    provider_contract: dict[str, Any],
    worker_heartbeat: dict[str, Any],
    provider_fetch: dict[str, Any],
    provider_case_cleanup: dict[str, Any],
    provider_origin_cleanup: dict[str, Any],
    worker_cleanup: dict[str, Any],
    commercial_cleanup: dict[str, Any],
    now: datetime | None = None,
    allowed_provider_tested_source_shas: set[str] | None = None,
) -> dict[str, Any]:
    source = _exact_sha40(source_sha, label="Stage-5 source SHA")
    contract_hash = _exact_sha64(gate_contract_sha256, label="gate contract SHA-256")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Stage-5 evidence timestamp must be timezone-aware")
    pr_runtime = _one_evidence_runtime(
        pr_evidence,
        expected_case_ids=expected_pr_case_ids,
        source_sha=source,
        gate_contract_sha256=contract_hash,
        label="PR",
    )
    identity_runtime = _one_evidence_runtime(
        identity_evidence,
        expected_case_ids=set(IDENTITY_CASE_IDS),
        source_sha=source,
        gate_contract_sha256=contract_hash,
        label="Preview Identity",
    )
    if (
        identity_cleanup.get("state") != "CLEANED"
        or identity_cleanup.get("source_sha") != source
        or identity_cleanup.get("runtime_bundle_id") != identity_runtime
    ):
        raise ValueError("Preview Identity cleanup is not exact and terminal")

    commercial_runtime = _exact_runtime(
        activation.get("runtime_bundle_id"), label="Preview Commercial runtime"
    )
    activation_id = _exact_uuid(
        activation.get("activation_id"), label="Preview Commercial activation ID"
    )
    api_deployment_id = str(activation.get("api_deployment_id") or "")
    worker_deployment_id = str(activation.get("worker_deployment_id") or "")
    worker_digest = str(activation.get("worker_image_digest") or "")
    if (
        activation.get("source_sha") != source
        or not _DEPLOYMENT.fullmatch(api_deployment_id)
        or not _DEPLOYMENT.fullmatch(worker_deployment_id)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", worker_digest)
    ):
        raise ValueError("Preview Commercial activation coordinates are invalid")
    runtime_coordinates = {
        "source_sha": source,
        "runtime_bundle_id": commercial_runtime,
        "api_deployment_id": api_deployment_id,
        "worker_deployment_id": worker_deployment_id,
        "worker_image_digest": worker_digest,
    }
    heartbeat_payload = worker_heartbeat.get("heartbeat") if isinstance(worker_heartbeat, dict) else None
    if worker_heartbeat.get("state") != "RUNNING" or not isinstance(heartbeat_payload, dict):
        raise ValueError("Worker heartbeat report is not RUNNING")
    _require_coordinates(heartbeat_payload, runtime_coordinates, label="Worker heartbeat")

    tested_source = _exact_sha40(
        provider_contract.get("tested_source_sha"), label="Provider tested source SHA"
    )
    allowed_tested = allowed_provider_tested_source_shas or {source}
    provider_evidence_hash = _exact_sha64(
        provider_contract.get("test_evidence_sha256"), label="Provider evidence SHA-256"
    )
    if provider_contract.get("state") != "VERIFIED" or tested_source not in allowed_tested:
        raise ValueError("Provider sandbox contract is not verified for this source lineage")
    if provider_fetch.get("passed") is not True:
        raise ValueError("Provider fetch report is not PASS")
    _require_coordinates(
        provider_fetch,
        {"activation_id": activation_id, **runtime_coordinates},
        label="Provider fetch",
    )
    if (
        provider_fetch.get("provider_contract_evidence_sha256") != provider_evidence_hash
        or provider_fetch.get("provider_fetch_count") != 1
        or provider_fetch.get("provider_task_terminal_status") not in {"completed", "failed"}
    ):
        raise ValueError("Provider fetch does not prove one read and a terminal task")

    _require_coordinates(
        provider_case_cleanup,
        {"state": "CLEANED", "activation_id": activation_id},
        label="Provider case cleanup",
    )
    _require_coordinates(
        provider_origin_cleanup,
        {"state": "REMOVED", "activation_id": activation_id},
        label="Provider origin cleanup",
    )
    _require_coordinates(
        worker_cleanup,
        {
            "state": "STOPPED",
            "source_sha": source,
            "runtime_bundle_id": commercial_runtime,
            "api_deployment_id": api_deployment_id,
            "worker_deployment_id": worker_deployment_id,
            "worker_image_digest": worker_digest,
            "heartbeat_state": "ABSENT",
        },
        label="Worker cleanup",
    )
    _require_coordinates(
        commercial_cleanup,
        {
            "state": "CLEANED",
            "activation_id": activation_id,
            "source_sha": source,
            "runtime_bundle_id": commercial_runtime,
            "api_deployment_id": api_deployment_id,
            "worker_deployment_id": worker_deployment_id,
        },
        label="Preview Commercial cleanup",
    )
    primary_bindings = {
        "pr": pr_runtime,
        "preview_identity": identity_runtime,
        "preview_commercial": commercial_runtime,
    }
    bindings = {
        **primary_bindings,
        "stage5_composite": composite_runtime_bundle_id(primary_bindings),
    }
    evidence = [
        _gate_evidence(
            "preview_provider_sandbox_contract",
            source_sha=source,
            runtime_bundle_id=commercial_runtime,
            gate_contract_sha256=contract_hash,
            now=current,
        ),
        _gate_evidence(
            "preview_worker_heartbeat",
            source_sha=source,
            runtime_bundle_id=commercial_runtime,
            gate_contract_sha256=contract_hash,
            now=current,
        ),
        _gate_evidence(
            "preview_provider_fetch",
            source_sha=source,
            runtime_bundle_id=commercial_runtime,
            gate_contract_sha256=contract_hash,
            now=current,
        ),
        _gate_evidence(
            "preview_cleanup",
            source_sha=source,
            runtime_bundle_id=bindings["stage5_composite"],
            gate_contract_sha256=contract_hash,
            now=current,
        ),
    ]
    return {"runtime_bindings": bindings, "commercial_evidence": evidence}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _case_rows(root: Path, case_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id in sorted(case_ids):
        matches = list(root.rglob(f"{case_id}.json"))
        if len(matches) != 1:
            raise ValueError(f"evidence artifact must contain one {case_id}.json")
        rows.append(_load(matches[0]))
    return rows


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _write_job_env(path: Path, bindings: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for scope, runtime in sorted(bindings.items()):
            handle.write(f"STAGE5_{scope.upper()}_RUNTIME_BUNDLE_ID={runtime}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--gate-contract", default="release/gates.json")
    parser.add_argument("--provider-contract", default="release/provider-contracts.json")
    parser.add_argument("--pr-root", required=True)
    parser.add_argument("--identity-root", required=True)
    parser.add_argument("--identity-cleanup-report", required=True)
    parser.add_argument("--commercial-root", required=True)
    parser.add_argument("--output-base", required=True)
    parser.add_argument("--job-env", required=True)
    args = parser.parse_args()
    try:
        gate_path = Path(args.gate_contract)
        gate = _load(gate_path)
        cases = {str(case["id"]): case for case in gate.get("cases", []) if isinstance(case, dict)}
        pr_ids = {case_id for case_id in gate["profiles"]["stage5_foundation"] if cases[case_id]["runtime_scope"] == "pr"}
        provider_document = _load(Path(args.provider_contract))
        provider_contract = provider_document["contracts"]["EVOLINK_SUBMISSION_RECONCILIATION"]
        tested_source = str(provider_contract.get("tested_source_sha") or "")
        _git_activation_lineage(args.source_sha, tested_source)
        commercial_root = Path(args.commercial_root)
        result = build_stage5_materialization(
            source_sha=args.source_sha,
            gate_contract_sha256=hashlib.sha256(gate_path.read_bytes()).hexdigest(),
            pr_evidence=_case_rows(Path(args.pr_root), pr_ids),
            expected_pr_case_ids=pr_ids,
            identity_evidence=_case_rows(Path(args.identity_root), set(IDENTITY_CASE_IDS)),
            identity_cleanup=_load(Path(args.identity_cleanup_report)),
            activation=_load(commercial_root / "activation-report.json"),
            provider_contract=provider_contract,
            worker_heartbeat=_load(commercial_root / "worker-heartbeat.json"),
            provider_fetch=_load(commercial_root / "provider-fetch.json"),
            provider_case_cleanup=_load(commercial_root / "provider-case-cleanup.json"),
            provider_origin_cleanup=_load(commercial_root / "provider-origin-removed.json"),
            worker_cleanup=_load(commercial_root / "worker-stopped.json"),
            commercial_cleanup=_load(commercial_root / "cleanup-report.json"),
            allowed_provider_tested_source_shas={tested_source},
        )
        output = Path(args.output_base)
        for row in _case_rows(Path(args.pr_root), pr_ids):
            path = output / "01-ci" / f'{row["case_id"]}.json'
            validate_evidence_path(path.as_posix())
            _write(path, row)
        for row in _case_rows(Path(args.identity_root), set(IDENTITY_CASE_IDS)):
            path = output / "02-integration" / f'{row["case_id"]}.json'
            validate_evidence_path(path.as_posix())
            _write(path, row)
        for row in result["commercial_evidence"]:
            phase = "08-cleanup" if row["case_id"] == "preview_cleanup" else "02-integration"
            path = output / phase / f'{row["case_id"]}.json'
            validate_evidence_path(path.as_posix())
            _write(path, row)
        _write_job_env(Path(args.job_env), result["runtime_bindings"])
        print(json.dumps({"state": "MATERIALIZED", "runtime_bindings": result["runtime_bindings"]}, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
