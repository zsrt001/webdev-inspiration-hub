"""Code-versioned external Provider capability authority.

Runtime environment variables cannot activate these capabilities. Any malformed,
missing, or incomplete committed contract is interpreted as UNVERIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


class ProviderContractState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


@dataclass(frozen=True, slots=True)
class ProviderContract:
    provider: str
    capability: str
    state: ProviderContractState
    endpoint_schema_sha256: str | None
    test_evidence_sha256: str | None
    official_source_url: str | None = None
    official_version: str | None = None
    official_contract_sha256: str | None = None
    tested_source_sha: str | None = None
    evidence_created_at: str | None = None
    approval_ref: str | None = None
    correlation_semantics: str | None = None
    idempotency_semantics: str | None = None


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "release" / "provider-contracts.json"
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _official_source_valid(value: Any, *, provider: str, capability: str) -> bool:
    try:
        source = urlsplit(str(value or ""))
        port = source.port
    except ValueError:
        return False
    expected_host = {
        "creem": "docs.creem.io",
        "evolink": "docs.evolink.ai",
    }.get(provider)
    if (
        source.scheme != "https"
        or source.hostname != expected_host
        or port not in {None, 443}
        or source.username
        or source.password
    ):
        return False
    if capability == "refund_creation":
        return (
            "/api-reference/endpoint/" in source.path
            and "refund" in source.path.lower()
        )
    return True


def _load_document() -> dict[str, Any]:
    try:
        document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(document, dict)
        or document.get("schema") != "vowpic.provider-contracts.v1"
        or not isinstance(document.get("contracts"), dict)
    ):
        return {}
    return document["contracts"]


_CONTRACTS = _load_document()


def _contract(name: str, *, provider: str, capability: str) -> ProviderContract:
    raw = _CONTRACTS.get(name)
    if not isinstance(raw, dict) or raw.get("provider") != provider or raw.get("capability") != capability:
        return ProviderContract(provider, capability, ProviderContractState.UNVERIFIED, None, None)
    try:
        state = ProviderContractState(str(raw.get("state") or "UNVERIFIED"))
    except ValueError:
        state = ProviderContractState.UNVERIFIED
    endpoint = str(raw.get("endpoint_schema_sha256") or "").strip().lower() or None
    evidence = str(raw.get("test_evidence_sha256") or "").strip().lower() or None
    official_hash = str(raw.get("official_contract_sha256") or "").strip().lower() or None
    tested_sha = str(raw.get("tested_source_sha") or "").strip().lower() or None
    verified_fields_valid = (
        endpoint is not None
        and evidence is not None
        and official_hash is not None
        and tested_sha is not None
        and _SHA64.fullmatch(endpoint) is not None
        and _SHA64.fullmatch(evidence) is not None
        and _SHA64.fullmatch(official_hash) is not None
        and _SHA40.fullmatch(tested_sha) is not None
        and _official_source_valid(
            raw.get("official_source_url"),
            provider=provider,
            capability=capability,
        )
        and bool(str(raw.get("official_version") or "").strip())
        and bool(str(raw.get("approval_ref") or "").strip())
    )
    if state is ProviderContractState.VERIFIED and not verified_fields_valid:
        state = ProviderContractState.UNVERIFIED
    return ProviderContract(
        provider=provider,
        capability=capability,
        state=state,
        endpoint_schema_sha256=endpoint,
        test_evidence_sha256=evidence,
        official_source_url=str(raw.get("official_source_url") or "").strip() or None,
        official_version=str(raw.get("official_version") or "").strip() or None,
        official_contract_sha256=official_hash,
        tested_source_sha=tested_sha,
        evidence_created_at=str(raw.get("evidence_created_at") or "").strip() or None,
        approval_ref=str(raw.get("approval_ref") or "").strip() or None,
        correlation_semantics=str(raw.get("correlation_semantics") or "").strip() or None,
        idempotency_semantics=str(raw.get("idempotency_semantics") or "").strip() or None,
    )


CREEM_REFUND_CREATION = _contract(
    "CREEM_REFUND_CREATION", provider="creem", capability="refund_creation"
)
CREEM_SUBSCRIPTION_PAID_TRANSACTION = _contract(
    "CREEM_SUBSCRIPTION_PAID_TRANSACTION",
    provider="creem",
    capability="subscription_paid_transaction",
)
CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION = _contract(
    "CREEM_SUBSCRIPTION_PERIOD_END_CANCELLATION",
    provider="creem",
    capability="subscription_period_end_cancellation",
)
EVOLINK_SUBMISSION_RECONCILIATION = _contract(
    "EVOLINK_SUBMISSION_RECONCILIATION",
    provider="evolink",
    capability="submission_reconciliation",
)


def require_verified_provider_contract(contract: ProviderContract) -> None:
    if (
        contract.state is not ProviderContractState.VERIFIED
        or not contract.endpoint_schema_sha256
        or not contract.test_evidence_sha256
        or not contract.official_contract_sha256
        or not contract.tested_source_sha
    ):
        raise RuntimeError(f"provider_contract_unverified:{contract.provider}:{contract.capability}")
