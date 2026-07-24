#!/usr/bin/env python3
"""Build a canonical, backend-role-discriminated pre-deployment runtime identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


DOMAIN_BY_ROLE = {
    "SAFE_BASELINE": "vowpic.runtime-bundle.safe-baseline.v2",
    "PREVIEW_IDENTITY": "vowpic.runtime-bundle.preview-identity.v1",
    "PREVIEW_COMMERCIAL": "vowpic.runtime-bundle.preview-commercial.v1",
    "COMMERCIAL_7A": "vowpic.runtime-bundle.commercial-7a.v1",
    "CONTRACT_7B": "vowpic.runtime-bundle.contract-7b.v1",
}

COMMON_REQUIRED = {
    "source_sha",
    "schema_revision",
    "migration_checksums",
    "contract_hashes",
    "tool_version",
}
ROLE_REQUIRED = {
    "SAFE_BASELINE": COMMON_REQUIRED | {"builder_contract_version"},
    "PREVIEW_IDENTITY": COMMON_REQUIRED | {"api_version"},
    "PREVIEW_COMMERCIAL": COMMON_REQUIRED,
    "COMMERCIAL_7A": COMMON_REQUIRED | {"builder_contract_version"},
    "CONTRACT_7B": COMMON_REQUIRED
    | {
        "builder_contract_version",
        "schema_before",
        "schema_target",
        "contract_migration_sha256",
        "compatibility_version",
    },
}
ROLE_OPTIONAL = {
    "SAFE_BASELINE": set(),
    "PREVIEW_IDENTITY": {"builder_contract_version"},
    "PREVIEW_COMMERCIAL": {"builder_contract_version", "api_version"},
    "COMMERCIAL_7A": {"api_version"},
    "CONTRACT_7B": {"api_version"},
}
BACKEND_BASE_CONTRACT_KEYS = {
    "payload", "provider", "model", "policy", "catalog", "flag", "gate", "activation", "runtime"
}
BACKEND_CONTRACT_KEYS_BY_ROLE = {
    "PREVIEW_COMMERCIAL": BACKEND_BASE_CONTRACT_KEYS | {"preview", "database_roles"},
    "COMMERCIAL_7A": BACKEND_BASE_CONTRACT_KEYS | {"database_roles"},
    "CONTRACT_7B": BACKEND_BASE_CONTRACT_KEYS | {"database_roles"},
}
FORBIDDEN_LIVE_KEYS = {
    "deployment_id",
    "api_deployment_id",
    "api_prebuilt_checksum",
    "current_snapshot_hash",
    "target_snapshot_hash",
    "resolved_snapshot",
    "manifest_sha256",
    "final_manifest_sha256",
    "evidence_sha256",
    "report_sha256",
    "live_state",
}


def _validate_sha(value: object, *, lengths: tuple[int, ...] = (64,)) -> str:
    text = str(value or "").strip().lower()
    if len(text) not in lengths or not re.fullmatch(r"[0-9a-f]+", text):
        raise ValueError("expected a lowercase hexadecimal digest")
    return text


def _validate_payload(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    if role not in DOMAIN_BY_ROLE:
        raise ValueError(f"unsupported release role: {role}")
    forbidden = FORBIDDEN_LIVE_KEYS & set(payload)
    if forbidden:
        raise ValueError(f"live/deployment coordinates are forbidden: {', '.join(sorted(forbidden))}")
    required = ROLE_REQUIRED[role]
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing runtime-bundle inputs: {', '.join(sorted(missing))}")
    allowed = required | ROLE_OPTIONAL[role]
    unexpected = set(payload) - allowed
    if unexpected:
        raise ValueError(f"inputs are not allowlisted for {role}: {', '.join(sorted(unexpected))}")

    normalized = dict(payload)
    normalized["source_sha"] = _validate_sha(payload["source_sha"], lengths=(40, 64))
    schema_revision = str(payload["schema_revision"] or "").strip()
    if not re.fullmatch(r"[0-9]{8}_[0-9]{4}", schema_revision):
        raise ValueError("schema_revision must be an Alembic revision")
    normalized["schema_revision"] = schema_revision

    migrations = payload["migration_checksums"]
    if not isinstance(migrations, list) or not migrations:
        raise ValueError("migration_checksums must be a non-empty ordered list")
    normalized_migrations = []
    revisions: set[str] = set()
    for item in migrations:
        if not isinstance(item, dict) or set(item) != {"revision", "sha256"}:
            raise ValueError("each migration checksum requires revision and sha256 only")
        revision = str(item["revision"] or "").strip()
        if not revision or revision in revisions:
            raise ValueError("migration revisions must be non-empty and unique")
        revisions.add(revision)
        normalized_migrations.append({"revision": revision, "sha256": _validate_sha(item["sha256"])})
    normalized["migration_checksums"] = normalized_migrations

    contracts = payload["contract_hashes"]
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError("contract_hashes must be a non-empty object")
    normalized["contract_hashes"] = {
        str(name): _validate_sha(value) for name, value in sorted(contracts.items())
    }
    if role == "SAFE_BASELINE" and set(contracts) != {"safe_baseline"}:
        raise ValueError("SAFE_BASELINE accepts only the safe_baseline contract")
    if role == "SAFE_BASELINE":
        if schema_revision != "20260712_0014":
            raise ValueError("SAFE_BASELINE is fixed to schema 20260712_0014")
        expected_revisions = ["20260710_0013", "20260712_0014"]
        if [item["revision"] for item in normalized_migrations] != expected_revisions:
            raise ValueError("SAFE_BASELINE requires ordered 0013 and 0014 migration checksums")
        if str(payload["builder_contract_version"]).strip() != "safe-baseline.v2":
            raise ValueError("SAFE_BASELINE builder contract must be safe-baseline.v2")
    if role == "PREVIEW_IDENTITY" and set(contracts) != {"identity_session_flag_preview"}:
        raise ValueError("PREVIEW_IDENTITY accepts only the identity/session/flag preview contract")
    if role in {"PREVIEW_COMMERCIAL", "COMMERCIAL_7A", "CONTRACT_7B"}:
        expected_contracts = BACKEND_CONTRACT_KEYS_BY_ROLE[role]
        missing_contracts = expected_contracts - set(contracts)
        unexpected_contracts = set(contracts) - expected_contracts
        if missing_contracts:
            raise ValueError(f"backend role is missing contracts: {', '.join(sorted(missing_contracts))}")
        if unexpected_contracts:
            raise ValueError(
                f"backend role has unrecognized contracts: {', '.join(sorted(unexpected_contracts))}"
            )
    if role == "CONTRACT_7B":
        if payload["schema_before"] == payload["schema_target"]:
            raise ValueError("CONTRACT_7B requires distinct before and target revisions")
        normalized["contract_migration_sha256"] = _validate_sha(payload["contract_migration_sha256"])
    for key in ("tool_version", "builder_contract_version", "api_version", "compatibility_version"):
        if key in normalized and not str(normalized[key] or "").strip():
            raise ValueError(f"{key} must be non-empty")
        if key in normalized:
            normalized[key] = str(normalized[key]).strip()
    return normalized


def compute_runtime_bundle_id(role: str, payload: dict[str, Any]) -> str:
    normalized_role = str(role or "").strip().upper()
    normalized = _validate_payload(normalized_role, payload)
    envelope = {
        "domain": DOMAIN_BY_ROLE[normalized_role],
        "release_role": normalized_role,
        "inputs": normalized,
    }
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "rtb_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _contract_arg(value: str) -> tuple[str, str]:
    name, separator, path = value.partition("=")
    if not separator or not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("contracts must use NAME=PATH")
    return name.strip(), path.strip()


def _runtime_contract_hashes(path: str, *, schema_revision: str) -> dict[str, str]:
    contract_path = Path(path).resolve()
    try:
        document = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("runtime contract is unreadable") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != "vowpic.runtime-contracts.v1"
        or document.get("schema_revision") != schema_revision
        or not isinstance(document.get("source_sha256"), dict)
    ):
        raise ValueError("runtime contract schema/revision is invalid")
    sources = document["source_sha256"]
    required_sources = {
        "backend/app/models/generation_job.py",
        "backend/app/core/config.py",
        "backend/app/services/generation_executor_service.py",
        "backend/app/services/generation_service.py",
        "backend/app/services/qa_pipeline.py",
    }
    missing = required_sources - set(sources)
    if missing:
        raise ValueError(f"runtime contract sources are missing: {', '.join(sorted(missing))}")
    validated: dict[str, str] = {}
    for raw_name, raw_digest in sorted(sources.items()):
        name = str(raw_name or "").strip().replace("\\", "/")
        if not name or name != raw_name:
            raise ValueError("runtime contract source name is invalid")
        validated[name] = _validate_sha(raw_digest)
    repository_root = next(
        (
            parent
            for parent in contract_path.parents
            if (parent / "backend").is_dir() and (parent / "scripts").is_dir()
        ),
        None,
    )
    if repository_root is None:
        raise ValueError("runtime contract is not inside the VowPic repository")
    for name, expected_digest in validated.items():
        source_path = (repository_root / name).resolve()
        try:
            source_path.relative_to(repository_root)
        except ValueError as exc:
            raise ValueError("runtime contract source path escapes the repository") from exc
        if not source_path.is_file():
            raise ValueError(f"runtime contract source is missing: {name}")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_digest:
            raise ValueError(f"runtime contract source digest is stale: {name}")
    model_payload = {
        name: validated[name]
        for name in ("backend/app/core/config.py", "backend/app/services/generation_service.py")
    }
    model_hash = hashlib.sha256(
        json.dumps(model_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "payload": validated["backend/app/models/generation_job.py"],
        "model": model_hash,
        "policy": validated["backend/app/services/qa_pipeline.py"],
        "runtime": _file_sha256(str(contract_path)),
    }


def _planned_commercial_contracts(args: argparse.Namespace) -> dict[str, str]:
    planned_paths = {
        "runtime": args.runtime_contract,
        "provider": args.provider_contract,
        "catalog": args.catalog_contract,
        "flag": args.flag_contract,
        "activation": args.activation_plan,
        "database_roles": args.database_role_contract,
    }
    if not any(planned_paths.values()) and not args.preview_contract:
        return {}
    missing = [name for name, path in planned_paths.items() if not path]
    if args.release_role == "PREVIEW_COMMERCIAL" and not args.preview_contract:
        missing.append("preview")
    if missing:
        raise ValueError(f"planned commercial contract paths are missing: {', '.join(sorted(missing))}")
    if args.release_role in {"COMMERCIAL_7A", "CONTRACT_7B"} and args.preview_contract:
        raise ValueError("Production runtime IDs cannot bind a Preview contract")
    contracts = _runtime_contract_hashes(args.runtime_contract, schema_revision=args.schema)
    contracts.update(
        {
            "provider": _file_sha256(args.provider_contract),
            "catalog": _file_sha256(args.catalog_contract),
            "flag": _file_sha256(args.flag_contract),
            "gate": _file_sha256(args.flag_contract),
            "activation": _file_sha256(args.activation_plan),
            "database_roles": _file_sha256(args.database_role_contract),
        }
    )
    if args.release_role == "PREVIEW_COMMERCIAL":
        contracts["preview"] = _file_sha256(args.preview_contract)
    return contracts


def _build_cli_payload(args: argparse.Namespace) -> dict[str, Any]:
    role = args.release_role
    migrations = []
    if args.ops_migration:
        migrations.append({"revision": "20260710_0013", "sha256": _file_sha256(args.ops_migration)})
    for revision, path in args.migration:
        migrations.append({"revision": revision, "sha256": _file_sha256(path)})
    contracts = {name: _file_sha256(path) for name, path in args.contract}
    planned_contracts = _planned_commercial_contracts(args)
    duplicate_contracts = set(contracts) & set(planned_contracts)
    if duplicate_contracts:
        raise ValueError(f"contract inputs are duplicated: {', '.join(sorted(duplicate_contracts))}")
    contracts.update(planned_contracts)
    if args.safe_baseline_contract:
        contracts["safe_baseline"] = _file_sha256(args.safe_baseline_contract)
    if planned_contracts and not migrations:
        raise ValueError(
            "commercial runtime IDs require explicit ordered migration checksums"
        )
    payload: dict[str, Any] = {
        "source_sha": args.source_sha,
        "schema_revision": args.schema,
        "migration_checksums": migrations,
        "contract_hashes": contracts,
        "tool_version": args.tool_version,
    }
    for name in (
        "builder_contract_version", "api_version",
        "schema_before", "schema_target", "compatibility_version",
    ):
        value = getattr(args, name)
        if value:
            payload[name] = value
    if args.contract_migration:
        payload["contract_migration_sha256"] = _file_sha256(args.contract_migration)
    if role == "CONTRACT_7B" and not payload.get("schema_revision"):
        payload["schema_revision"] = payload.get("schema_target")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-role", required=True, choices=sorted(DOMAIN_BY_ROLE))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--schema", default="")
    parser.add_argument("--ops-migration")
    parser.add_argument("--migration", action="append", default=[], type=_contract_arg, metavar="REVISION=PATH")
    parser.add_argument("--safe-baseline-contract")
    parser.add_argument("--contract", action="append", default=[], type=_contract_arg, metavar="NAME=PATH")
    parser.add_argument("--runtime-contract")
    parser.add_argument("--preview-contract")
    parser.add_argument("--provider-contract")
    parser.add_argument("--catalog-contract")
    parser.add_argument("--flag-contract")
    parser.add_argument("--activation-plan")
    parser.add_argument("--database-role-contract")
    parser.add_argument("--builder-contract-version")
    parser.add_argument("--api-version")
    parser.add_argument("--tool-version", default="vowpic-release-tools.v1")
    parser.add_argument("--schema-before")
    parser.add_argument("--schema-target")
    parser.add_argument("--contract-migration")
    parser.add_argument("--compatibility-version")
    parser.add_argument("--output")
    args = parser.parse_args()
    runtime_id = compute_runtime_bundle_id(args.release_role, _build_cli_payload(args))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(runtime_id + "\n", encoding="utf-8")
    print(runtime_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
