"""Sanitized public runtime-bundle facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_CONTRACT = ROOT / "release" / "runtime-contracts.json"
SAFE_BASELINE_CONTRACT = ROOT / "release" / "safe-baseline-contract.json"
ACTIVATION_CONTRACT = ROOT / "release" / "activation-plan.json"
PROVIDER_CONTRACT = ROOT / "release" / "provider-contracts.json"


@dataclass(frozen=True)
class PublicRuntimeBundle:
    schema: str
    source_sha: str
    runtime_bundle_id: str
    deployment_id: str
    release_role: str
    runtime_environment: str
    schema_revision: str
    api_compatibility_version: str
    worker_compatibility_version: str
    job_payload_min: str
    job_payload_max: str
    worker_image_digest: str
    provider_policy_hash: str
    flag_contract_hash: str


def _read_runtime_contract() -> dict[str, Any]:
    try:
        payload = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != "vowpic.runtime-contracts.v1":
        return {}
    return payload


def _read_safe_baseline_contract() -> dict[str, Any]:
    try:
        payload = json.loads(SAFE_BASELINE_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("contract_version") != "safe-baseline.v2"
    ):
        return {}
    return payload


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def public_runtime_bundle(settings_obj: Settings | None = None) -> PublicRuntimeBundle:
    """Return only immutable, non-secret coordinates safe for public attestation."""
    active_settings = settings_obj or get_settings()
    contract = _read_runtime_contract()
    release_role = active_settings.release_role.strip()
    if release_role == "SAFE_BASELINE":
        schema_revision = str(
            _read_safe_baseline_contract().get("schema_revision") or ""
        )
    else:
        schema_revision = str(contract.get("schema_revision") or "")
    payload_version = str(contract.get("job_payload_version") or "")
    return PublicRuntimeBundle(
        schema="vowpic.runtime-bundle-report.v1",
        source_sha=active_settings.source_sha,
        runtime_bundle_id=active_settings.runtime_bundle_id.strip().lower(),
        deployment_id=active_settings.deployment_id,
        release_role=release_role,
        runtime_environment=active_settings.runtime_environment,
        schema_revision=schema_revision,
        api_compatibility_version=str(contract.get("api_compatibility_version") or ""),
        worker_compatibility_version=str(contract.get("worker_compatibility_version") or ""),
        job_payload_min=payload_version,
        job_payload_max=payload_version,
        worker_image_digest=active_settings.worker_image_digest.strip().lower(),
        provider_policy_hash=_file_sha256(PROVIDER_CONTRACT),
        flag_contract_hash=_file_sha256(ACTIVATION_CONTRACT),
    )


def public_runtime_bundle_json(settings_obj: Settings | None = None) -> dict[str, str]:
    return asdict(public_runtime_bundle(settings_obj))
