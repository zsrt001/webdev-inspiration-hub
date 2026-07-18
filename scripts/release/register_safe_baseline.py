#!/usr/bin/env python3
"""Reserve and CAS-advance the one-time Production safe-baseline install."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import quote, urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
import httpx  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from scripts.release.github_artifact_evidence import parse_reference  # noqa: E402
from scripts.release.production_inventory_rls import reconcile_inventory_rls_policies  # noqa: E402

from app.services.production_inventory_service import (  # noqa: E402
    ProductionInventoryReport,
    validate_read_only_proof,
)


NOT_RUN_EXIT = 3
OLD_SCHEMA = "20260516_0012"
TARGET_SCHEMA = "20260712_0014"
ACTIVATION_KIND = "SAFE_BASELINE_INSTALL"
RESERVATION_TTL_MINUTES = 120
BUILD_ARTIFACT_RECOVERY_DAYS = 90
ADOPTABLE_RESERVED_EMPTY_FIELDS = (
    "runtime_bundle_id",
    "manifest_sha256",
    "build_artifact_id",
    "build_artifact_digest",
    "report_sha256",
    "api_deployment_id",
    "api_deployment_url",
    "api_role",
    "worker_deployment_id",
    "worker_role",
    "worker_image_digest",
    "target_snapshot_hash",
    "acceptance_fault_intent_id",
    "acceptance_fault_intent_sha256",
    "acceptance_fault_state",
    "acceptance_fault_expires_at",
    "acceptance_fault_cleanup_claim_id",
    "acceptance_fault_cleanup_fencing_token",
)
STAGED_TAKEOVER_EMPTY_FIELDS = (
    "report_sha256",
    "worker_deployment_id",
    "worker_role",
    "worker_image_digest",
    "target_snapshot_hash",
    "acceptance_fault_intent_id",
    "acceptance_fault_intent_sha256",
    "acceptance_fault_state",
    "acceptance_fault_expires_at",
    "acceptance_fault_cleanup_claim_id",
    "acceptance_fault_cleanup_fencing_token",
)
RESERVED_BUILD_REARM_EMPTY_FIELDS = (
    "runtime_bundle_id",
    "report_sha256",
    "api_deployment_id",
    "api_deployment_url",
    "api_role",
    "worker_deployment_id",
    "worker_role",
    "worker_image_digest",
    "target_snapshot_hash",
    "acceptance_fault_intent_id",
    "acceptance_fault_intent_sha256",
    "acceptance_fault_state",
    "acceptance_fault_expires_at",
    "acceptance_fault_cleanup_claim_id",
    "acceptance_fault_cleanup_fencing_token",
)
STAGED_TAKEOVER_REQUIRED_CONTROL_PATHS = frozenset(
    {
        ".github/workflows/safe-baseline-release.yml",
        "scripts/release/register_safe_baseline.py",
        "scripts/release/verify_safe_baseline.py",
    }
)
STAGED_TAKEOVER_ALLOWED_CONTROL_PATHS = frozenset(
    {
        *STAGED_TAKEOVER_REQUIRED_CONTROL_PATHS,
        "backend/tests/test_ci_release_contract.py",
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
RESERVED_BUILD_REPAIR_REQUIRED_PATHS = frozenset(
    {
        ".github/workflows/safe-baseline-release.yml",
        "scripts/release/register_safe_baseline.py",
        "scripts/release/verify_safe_baseline.py",
        "frontend/package.json",
        "frontend/package-lock.json",
    }
)
RESERVED_BUILD_REPAIR_ALLOWED_PATHS = frozenset(
    {
        *RESERVED_BUILD_REPAIR_REQUIRED_PATHS,
        "backend/tests/test_ci_release_contract.py",
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
RESERVED_DEPLOY_ROOT_REPAIR_PREVIOUS_SOURCE_SHA = (
    "9aadae87ceae13d5dd65b324d8460bec88c2fb21"
)
RESERVED_DEPLOY_ROOT_REPAIR_REQUIRED_PATHS = frozenset(
    {
        ".github/workflows/safe-baseline-release.yml",
        "scripts/release/register_safe_baseline.py",
        "backend/tests/test_ci_release_contract.py",
    }
)
RESERVED_DEPLOY_ROOT_REPAIR_ALLOWED_PATHS = frozenset(
    {
        *RESERVED_DEPLOY_ROOT_REPAIR_REQUIRED_PATHS,
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
RESERVED_VERCEL_PYTHON_REPAIR_PREVIOUS_SOURCE_SHA = (
    "6f5ef3936a527051c8cd7d242e0d3bed581ae011"
)
RESERVED_VERCEL_PYTHON_REPAIR_REQUIRED_PATHS = frozenset(
    {
        "scripts/release/register_safe_baseline.py",
        "backend/tests/test_ci_release_contract.py",
    }
)
RESERVED_VERCEL_PYTHON_REPAIR_ALLOWED_PATHS = frozenset(
    {
        *RESERVED_VERCEL_PYTHON_REPAIR_REQUIRED_PATHS,
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_PREVIOUS_SOURCE_SHA = (
    "744ee54a62343cc83c36d7903fc32a719c82dbe1"
)
RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_REQUIRED_PATHS = frozenset(
    {
        "scripts/release/register_safe_baseline.py",
        "backend/tests/test_ci_release_contract.py",
    }
)
RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_ALLOWED_PATHS = frozenset(
    {
        *RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_REQUIRED_PATHS,
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_PREVIOUS_SOURCE_SHA = (
    "0cf0296e90b46b5be43c91253a1f7c4e9b96f1a5"
)
RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_REQUIRED_PATHS = frozenset(
    {
        "scripts/release/register_safe_baseline.py",
        "backend/tests/test_ci_release_contract.py",
    }
)
RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_ALLOWED_PATHS = frozenset(
    {
        *RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_REQUIRED_PATHS,
        "docs/ai-worklog.md",
        "docs/operations/risk-lockdown-runbook.md",
    }
)
VERCEL_PUBLIC_ENV_EXAMPLE_REFERENCES = frozenset(
    {
        ".env.example",
        "backend/.env.example",
        "frontend/.env.example",
    }
)
ADM_ZIP_REPAIR_VERSION = "0.6.0"
ADM_ZIP_REPAIR_LOCK_ENTRY = {
    "version": ADM_ZIP_REPAIR_VERSION,
    "resolved": "https://registry.npmmirror.com/adm-zip/-/adm-zip-0.6.0.tgz",
    "integrity": (
        "sha512-XleryMhbuksdKtofnWZ9Sk+4CUTbms4Mb/EU32SZwToAyZ5RgVos/"
        "ki8n+yr0LWHOGKuakbXTuuYNHLQjhddgg=="
    ),
    "license": "MIT",
    "engines": {"node": ">=14.0"},
}
PHASE_RANK = {
    "RESERVED": 0,
    "STAGED": 10,
    "PROMOTION_ARMED": 15,
    "PROMOTED": 20,
    "FORMAL_VERIFIED": 30,
    "COMPLETED": 40,
}
PHASE_SEQUENCE = tuple(PHASE_RANK)
RETRIABLE_STATES = {f"RETRY_{phase}" for phase in PHASE_SEQUENCE if phase != "COMPLETED"}
MAX_DEPLOYMENT_PAGES = 100
MIGRATION_LOCK_TIMEOUT = "15s"
MIGRATION_STATEMENT_TIMEOUT = "5min"


class SafeBaselineRegistrationError(RuntimeError):
    pass


def _activation_value(activation: dict[str, Any], name: str) -> Any:
    return activation.get(name)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reservation_is_expired(
    activation: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if activation is None:
        return False
    expiry = _parse_datetime(_activation_value(activation, "reservation_expires_at"))
    return expiry is not None and expiry <= (now or datetime.now(timezone.utc))


def build_artifact_recovery_is_expired(
    activation: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if activation is None:
        return False
    created_at = _parse_datetime(_activation_value(activation, "created_at"))
    if created_at is None:
        return True
    recovery_deadline = created_at + timedelta(days=BUILD_ARTIFACT_RECOVERY_DAYS)
    return recovery_deadline <= (now or datetime.now(timezone.utc))


def reserved_install_is_adoptable(activation: dict[str, Any]) -> bool:
    """Allow a new reviewed run to adopt only a completely unbound reservation."""
    try:
        version = int(_activation_value(activation, "version") or 0)
        workflow_attempt = int(_activation_value(activation, "workflow_attempt") or 0)
    except (TypeError, ValueError):
        return False
    source_sha = str(_activation_value(activation, "source_sha") or "")
    workflow_run_id = str(_activation_value(activation, "workflow_run_id") or "")
    return (
        str(_activation_value(activation, "phase") or "") == "RESERVED"
        and version > 0
        and workflow_attempt > 0
        and re.fullmatch(r"[0-9a-f]{40,64}", source_sha) is not None
        and bool(workflow_run_id.strip())
        and all(_activation_value(activation, field) in (None, "") for field in ADOPTABLE_RESERVED_EMPTY_FIELDS)
    )


def staged_verifier_takeover_is_adoptable(activation: dict[str, Any]) -> bool:
    """Allow only release-control recovery of a fully bound, unpromoted STAGED row."""
    try:
        version = int(_activation_value(activation, "version") or 0)
        workflow_attempt = int(_activation_value(activation, "workflow_attempt") or 0)
    except (TypeError, ValueError):
        return False
    source_sha = str(_activation_value(activation, "source_sha") or "")
    workflow_run_id = str(_activation_value(activation, "workflow_run_id") or "")
    evidence_prefix = str(_activation_value(activation, "private_evidence_prefix") or "")
    deployment_id = str(_activation_value(activation, "api_deployment_id") or "")
    deployment_url = str(_activation_value(activation, "api_deployment_url") or "")
    build_artifact_id = str(_activation_value(activation, "build_artifact_id") or "")
    return (
        str(_activation_value(activation, "phase") or "") == "STAGED"
        and version > 0
        and workflow_attempt > 0
        and re.fullmatch(r"[0-9a-f]{40,64}", source_sha) is not None
        and re.fullmatch(r"[1-9][0-9]*", workflow_run_id) is not None
        and bool(str(_activation_value(activation, "approval") or "").strip())
        and re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
            r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
            evidence_prefix,
        )
        is not None
        and re.fullmatch(
            r"rtb_[0-9a-f]{64}",
            str(_activation_value(activation, "runtime_bundle_id") or ""),
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(_activation_value(activation, "manifest_sha256") or ""),
        )
        is not None
        and re.fullmatch(r"[1-9][0-9]*", build_artifact_id) is not None
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(_activation_value(activation, "build_artifact_digest") or ""),
        )
        is not None
        and bool(deployment_id)
        and len(deployment_id) <= 160
        and not any(char.isspace() for char in deployment_id)
        and re.fullmatch(r"https://[A-Za-z0-9-]+\.vercel\.app", deployment_url) is not None
        and str(_activation_value(activation, "api_role") or "") == "SAFE_BASELINE"
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(_activation_value(activation, "current_snapshot_hash") or ""),
        )
        is not None
        and all(
            _activation_value(activation, field) in (None, "")
            for field in STAGED_TAKEOVER_EMPTY_FIELDS
        )
    )


def reserved_build_rearm_is_adoptable(activation: dict[str, Any]) -> bool:
    """Allow one reviewed repair of a bound prebuild that was never deployed."""
    try:
        version = int(_activation_value(activation, "version") or 0)
        workflow_attempt = int(_activation_value(activation, "workflow_attempt") or 0)
    except (TypeError, ValueError):
        return False
    source_sha = str(_activation_value(activation, "source_sha") or "")
    workflow_run_id = str(_activation_value(activation, "workflow_run_id") or "")
    evidence_prefix = str(_activation_value(activation, "private_evidence_prefix") or "")
    return (
        str(_activation_value(activation, "phase") or "") == "RESERVED"
        and version > 0
        and workflow_attempt > 0
        and re.fullmatch(r"[0-9a-f]{40,64}", source_sha) is not None
        and re.fullmatch(r"[1-9][0-9]*", workflow_run_id) is not None
        and bool(str(_activation_value(activation, "approval") or "").strip())
        and re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
            r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
            evidence_prefix,
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(_activation_value(activation, "manifest_sha256") or ""),
        )
        is not None
        and re.fullmatch(
            r"[1-9][0-9]{0,19}",
            str(_activation_value(activation, "build_artifact_id") or ""),
        )
        is not None
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(_activation_value(activation, "build_artifact_digest") or ""),
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(_activation_value(activation, "current_snapshot_hash") or ""),
        )
        is not None
        and all(
            _activation_value(activation, field) in (None, "")
            for field in RESERVED_BUILD_REARM_EMPTY_FIELDS
        )
    )


def validate_descendant_source(previous_source_sha: str, source_sha: str) -> None:
    """Prove the reviewed checkout is the requested descendant before adoption."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != source_sha:
        raise SafeBaselineRegistrationError("reservation adoption source is not the reviewed checkout")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", previous_source_sha, source_sha],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if ancestry.returncode != 0:
        raise SafeBaselineRegistrationError(
            "reservation adoption source is not a descendant of the reserving source"
        )


def validate_staged_control_descendant(runtime_source_sha: str, runner_sha: str) -> None:
    """Prove a staged takeover changes release control only, never deployed inputs."""
    if (
        re.fullmatch(r"[0-9a-f]{40,64}", runtime_source_sha) is None
        or re.fullmatch(r"[0-9a-f]{40,64}", runner_sha) is None
        or runtime_source_sha == runner_sha
    ):
        raise SafeBaselineRegistrationError(
            "staged verifier takeover requires a distinct reviewed descendant runner"
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != runner_sha:
        raise SafeBaselineRegistrationError(
            "staged verifier takeover runner is not the reviewed checkout"
        )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", runtime_source_sha, runner_sha],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if ancestry.returncode != 0:
        raise SafeBaselineRegistrationError(
            "staged verifier takeover runner is not a descendant of the deployed source"
        )
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--no-renames",
            f"{runtime_source_sha}..{runner_sha}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        raise SafeBaselineRegistrationError("staged verifier takeover diff could not be proven")
    changed_paths: set[str] = set()
    for line in diff.stdout.splitlines():
        try:
            status_code, path = line.split("\t", 1)
        except ValueError as exc:
            raise SafeBaselineRegistrationError(
                "staged verifier takeover diff is malformed"
            ) from exc
        normalized = path.replace("\\", "/")
        if status_code != "M" or normalized not in STAGED_TAKEOVER_ALLOWED_CONTROL_PATHS:
            raise SafeBaselineRegistrationError(
                f"staged verifier takeover changed a non-control path: {normalized}"
            )
        changed_paths.add(normalized)
    missing = STAGED_TAKEOVER_REQUIRED_CONTROL_PATHS - changed_paths
    if missing:
        raise SafeBaselineRegistrationError(
            "staged verifier takeover is missing required reviewed control changes: "
            + ", ".join(sorted(missing))
        )


def validate_reserved_build_repair_descendant(
    previous_source_sha: str,
    source_sha: str,
) -> None:
    """Prove an undeployed bound prebuild advances through one exact repair diff."""
    if (
        re.fullmatch(r"[0-9a-f]{40,64}", previous_source_sha) is None
        or re.fullmatch(r"[0-9a-f]{40,64}", source_sha) is None
        or previous_source_sha == source_sha
    ):
        raise SafeBaselineRegistrationError(
            "reserved build repair requires a distinct reviewed descendant source"
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != source_sha:
        raise SafeBaselineRegistrationError(
            "reserved build repair source is not the reviewed checkout"
        )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", previous_source_sha, source_sha],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if ancestry.returncode != 0:
        raise SafeBaselineRegistrationError(
            "reserved build repair source is not a descendant of the bound source"
        )
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--no-renames",
            f"{previous_source_sha}..{source_sha}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        raise SafeBaselineRegistrationError("reserved build repair diff could not be proven")
    if previous_source_sha == RESERVED_DEPLOY_ROOT_REPAIR_PREVIOUS_SOURCE_SHA:
        required_paths = RESERVED_DEPLOY_ROOT_REPAIR_REQUIRED_PATHS
        allowed_paths = RESERVED_DEPLOY_ROOT_REPAIR_ALLOWED_PATHS
        validate_dependency_repair = False
    elif previous_source_sha == RESERVED_VERCEL_PYTHON_REPAIR_PREVIOUS_SOURCE_SHA:
        required_paths = RESERVED_VERCEL_PYTHON_REPAIR_REQUIRED_PATHS
        allowed_paths = RESERVED_VERCEL_PYTHON_REPAIR_ALLOWED_PATHS
        validate_dependency_repair = False
    elif (
        previous_source_sha
        == RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_PREVIOUS_SOURCE_SHA
    ):
        required_paths = RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_REQUIRED_PATHS
        allowed_paths = RESERVED_VERCEL_PYTHON_PYCACHE_REPAIR_ALLOWED_PATHS
        validate_dependency_repair = False
    elif previous_source_sha == RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_PREVIOUS_SOURCE_SHA:
        required_paths = RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_REQUIRED_PATHS
        allowed_paths = RESERVED_TRACKED_ENV_EXAMPLE_REPAIR_ALLOWED_PATHS
        validate_dependency_repair = False
    else:
        required_paths = RESERVED_BUILD_REPAIR_REQUIRED_PATHS
        allowed_paths = RESERVED_BUILD_REPAIR_ALLOWED_PATHS
        validate_dependency_repair = True

    changed_paths: set[str] = set()
    for line in diff.stdout.splitlines():
        try:
            status_code, path = line.split("\t", 1)
        except ValueError as exc:
            raise SafeBaselineRegistrationError(
                "reserved build repair diff is malformed"
            ) from exc
        normalized = path.replace("\\", "/")
        if status_code != "M" or normalized not in allowed_paths:
            raise SafeBaselineRegistrationError(
                f"reserved build repair changed an unauthorized path: {normalized}"
            )
        changed_paths.add(normalized)
    missing = required_paths - changed_paths
    if missing:
        raise SafeBaselineRegistrationError(
            "reserved build repair is missing required reviewed changes: "
            + ", ".join(sorted(missing))
        )
    if validate_dependency_repair:
        validate_reserved_build_dependency_repair(previous_source_sha, source_sha)


def _read_git_json(revision: str, path: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SafeBaselineRegistrationError(
            f"reserved build repair could not read {path} at {revision}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SafeBaselineRegistrationError(
            f"reserved build repair found invalid JSON in {path} at {revision}"
        ) from exc
    if not isinstance(payload, dict):
        raise SafeBaselineRegistrationError(
            f"reserved build repair expected an object in {path} at {revision}"
        )
    return payload


def validate_reserved_build_dependency_repair(
    previous_source_sha: str,
    source_sha: str,
) -> None:
    """Require the dependency diff to be only the exact adm-zip security override."""
    previous_package = _read_git_json(previous_source_sha, "frontend/package.json")
    source_package = _read_git_json(source_sha, "frontend/package.json")
    previous_overrides = previous_package.get("overrides")
    if not isinstance(previous_overrides, dict) or "adm-zip" in previous_overrides:
        raise SafeBaselineRegistrationError(
            "reserved build repair has an unexpected prior adm-zip override"
        )
    expected_package = copy.deepcopy(previous_package)
    expected_package["overrides"]["adm-zip"] = ADM_ZIP_REPAIR_VERSION
    if source_package != expected_package:
        raise SafeBaselineRegistrationError(
            "reserved build repair changed frontend/package.json beyond adm-zip"
        )

    previous_lock = _read_git_json(previous_source_sha, "frontend/package-lock.json")
    source_lock = _read_git_json(source_sha, "frontend/package-lock.json")
    previous_packages = previous_lock.get("packages")
    if not isinstance(previous_packages, dict):
        raise SafeBaselineRegistrationError(
            "reserved build repair prior package lock has no packages object"
        )
    previous_adm_zip = previous_packages.get("node_modules/adm-zip")
    if not isinstance(previous_adm_zip, dict) or previous_adm_zip.get("version") != "0.5.16":
        raise SafeBaselineRegistrationError(
            "reserved build repair prior package lock is not the reviewed adm-zip 0.5.16 graph"
        )
    expected_lock = copy.deepcopy(previous_lock)
    expected_lock["packages"]["node_modules/adm-zip"] = copy.deepcopy(
        ADM_ZIP_REPAIR_LOCK_ENTRY
    )
    if source_lock != expected_lock:
        raise SafeBaselineRegistrationError(
            "reserved build repair changed frontend/package-lock.json beyond adm-zip"
        )


def validate_resume_coordinates(
    activation: dict[str, Any],
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> None:
    if (
        str(_activation_value(activation, "source_sha") or "") != source_sha
        or str(_activation_value(activation, "workflow_run_id") or "") != workflow_run_id
    ):
        raise SafeBaselineRegistrationError(
            "safe-baseline coordinates do not match the reserving run"
        )
    current_attempt = int(_activation_value(activation, "workflow_attempt") or 1)
    if workflow_attempt < current_attempt:
        raise ValueError("workflow attempt cannot move backwards")


def classify_install_state(
    *,
    current_revision: str,
    activation: dict[str, Any] | None,
    source_sha: str,
    workflow_run_id: str,
    now: datetime | None = None,
) -> str:
    """Classify without mutating; terminal or inconsistent states fail closed."""
    if current_revision not in {OLD_SCHEMA, TARGET_SCHEMA}:
        return "UNSUPPORTED_SCHEMA"
    if activation is None:
        return "FRESH_INSTALL" if current_revision == OLD_SCHEMA else "ORPHANED_SCHEMA"
    phase = str(_activation_value(activation, "phase") or "")
    if current_revision == OLD_SCHEMA:
        return "INVALID_RESERVATION_WITH_OLD_SCHEMA"
    if phase == "COMPLETED":
        return "ALREADY_COMPLETED"
    activation_source_sha = str(_activation_value(activation, "source_sha") or "")
    activation_workflow_run_id = str(_activation_value(activation, "workflow_run_id") or "")
    if activation_source_sha != source_sha:
        if reserved_install_is_adoptable(activation):
            return "TAKEOVER_RESERVED"
        if reserved_build_rearm_is_adoptable(activation):
            return "TAKEOVER_RESERVED_BUILD"
        return "CONFLICTING_INSTALL"
    if activation_workflow_run_id != workflow_run_id:
        if reserved_install_is_adoptable(activation):
            return "TAKEOVER_RESERVED"
        if staged_verifier_takeover_is_adoptable(activation):
            return "TAKEOVER_STAGED"
        return "CONFLICTING_INSTALL"
    if phase not in PHASE_RANK:
        return "UNKNOWN_PHASE"
    # Expiry is an audit deadline, not permission for another run to take over.
    # The exact source/run owner can resume through the same fail-closed recovery
    # path after fresh protected-environment and edge evidence checks.
    reservation_is_expired(activation, now=now)
    return f"RETRY_{phase}"


def validate_phase_transition(current_phase: str, target_phase: str) -> None:
    if current_phase not in PHASE_RANK or target_phase not in PHASE_RANK:
        raise ValueError("unknown safe-baseline phase")
    current_index = PHASE_SEQUENCE.index(current_phase)
    if current_index + 1 >= len(PHASE_SEQUENCE) or PHASE_SEQUENCE[current_index + 1] != target_phase:
        raise ValueError(f"invalid safe-baseline phase transition: {current_phase} -> {target_phase}")


def recovery_decision(
    phase: str,
    *,
    candidate_count: int,
    formal_domain_matches: bool = False,
) -> str:
    if candidate_count < 0:
        raise ValueError("candidate count cannot be negative")
    if phase == "RESERVED":
        if candidate_count == 0:
            return "DEPLOY_ONCE"
        if candidate_count == 1:
            return "BIND_ONLY_CANDIDATE"
        return "MANUAL_FORWARD_DISPOSITION"
    if phase in {"STAGED", "PROMOTION_ARMED"}:
        return "REUSE_RECORDED_DEPLOYMENT"
    if phase == "PROMOTED":
        return "ADVANCE_FORMAL_VERIFICATION" if formal_domain_matches else "KEEP_EDGE_DENY"
    if phase in {"FORMAL_VERIFIED", "COMPLETED"}:
        return "ADVANCE_WITHOUT_REBUILD" if phase == "FORMAL_VERIFIED" else "STOP_COMPLETED"
    raise ValueError("unknown safe-baseline phase")


def promotion_recovery_decision(
    phase: str,
    *,
    formal_deployment_id: str | None,
    target_deployment_id: str,
) -> str:
    target = str(target_deployment_id or "").strip()
    if not target:
        raise ValueError("target deployment ID is required")
    matches = str(formal_deployment_id or "").strip() == target
    if phase == "PROMOTION_ARMED":
        return "ADVANCE_PROMOTED_WITHOUT_PROMOTE" if matches else "PROMOTE_ONCE"
    if phase == "PROMOTED":
        return "ADVANCE_FORMAL_VERIFICATION" if matches else "KEEP_EDGE_DENY"
    if phase == "FORMAL_VERIFIED":
        return "COMPLETE_WITHOUT_PROMOTE" if matches else "KEEP_EDGE_DENY"
    raise ValueError(
        "promotion recovery requires PROMOTION_ARMED, PROMOTED, or FORMAL_VERIFIED"
    )


def _validate_sha(value: str, *, name: str, lengths: tuple[int, ...] = (64,)) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) not in lengths or not re.fullmatch(r"[0-9a-f]+", clean):
        raise ValueError(f"{name} must be a lowercase hexadecimal digest")
    return clean


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("build output directory does not exist")
    root_metadata = path.lstat()
    entries: list[dict[str, str]] = [
        {
            "path": ".",
            "type": "directory",
            "mode": f"{stat.S_IMODE(root_metadata.st_mode):04o}",
        }
    ]
    children = sorted(
        path.rglob("*"),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    )
    if not children:
        raise ValueError("build output directory is empty")
    for item in children:
        metadata = item.lstat()
        entry = {
            "path": item.relative_to(path).as_posix(),
            "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        }
        if stat.S_ISLNK(metadata.st_mode):
            entry.update({"type": "symlink", "target": os.readlink(item)})
        elif stat.S_ISDIR(metadata.st_mode):
            entry["type"] = "directory"
        elif stat.S_ISREG(metadata.st_mode):
            entry.update({"type": "file", "sha256": _file_sha256(item)})
        else:
            raise ValueError(f"unsupported build output entry: {entry['path']}")
        entries.append(entry)
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def materialize_vercel_deploy_root(
    source_output: Path,
    *,
    source_root: Path,
    destination_root: Path,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    expected_project_id: str,
    expected_org_id: str,
) -> dict[str, Any]:
    """Create one self-contained Vercel prebuilt deployment root.

    `vercel build` may represent static assets as symlinks into the source tree.
    Function `.vc-config.json` files may also retain repository-relative
    `filePathMap` references that the CLI adds to the prebuilt upload. A durable
    artifact must contain both forms of referenced bytes at their exact paths.
    """
    source_sha = _validate_sha(source_sha, name="source SHA", lengths=(40, 64))
    runner_sha = _validate_sha(runner_sha, name="runner SHA", lengths=(40, 64))
    if not re.fullmatch(r"[1-9][0-9]*", str(workflow_run_id or "")):
        raise ValueError("workflow run ID must be a positive decimal ID")
    if workflow_attempt < 1:
        raise ValueError("workflow attempt must be positive")
    if not re.fullmatch(r"prj_[A-Za-z0-9]+", expected_project_id or ""):
        raise ValueError("expected Vercel project ID is invalid")
    if not re.fullmatch(r"(?:team|user)_[A-Za-z0-9]+", expected_org_id or ""):
        raise ValueError("expected Vercel organization ID is invalid")
    if source_output.is_symlink() or not source_output.is_dir():
        raise ValueError("Vercel build output directory does not exist")
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("immutable source root does not exist")
    if destination_root.exists() or destination_root.is_symlink():
        raise ValueError("materialized deploy root destination already exists")

    resolved_root = source_root.resolve(strict=True)
    resolved_output = source_output.resolve(strict=True)
    if not resolved_output.is_relative_to(resolved_root):
        raise ValueError("Vercel build output is outside the immutable source root")
    destination_parent = destination_root.parent.resolve(strict=True)
    if destination_parent == resolved_root or destination_parent.is_relative_to(resolved_root):
        raise ValueError("materialized deploy root must be outside the immutable source root")

    staging = destination_root.with_name(
        f".{destination_root.name}.materializing-{uuid.uuid4().hex}"
    )
    counts = {
        "directories": 0,
        "files": 0,
        "materialized_symlinks": 0,
        "reference_declarations": 0,
        "referenced_files": 0,
    }

    def checked_effective_path(path: Path) -> tuple[Path, bool]:
        is_link = path.is_symlink()
        try:
            effective = path.resolve(strict=True) if is_link else path
        except (OSError, RuntimeError) as exc:
            raise ValueError("Vercel build output contains a broken or cyclic symlink") from exc
        resolved = effective.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("Vercel build output symlink escapes the immutable source root")
        relative = resolved.relative_to(resolved_root)
        if not resolved.is_relative_to(resolved_output) and (
            (relative.parts and relative.parts[0] == ".vercel")
            or any(part == ".git" or part.startswith(".env") for part in relative.parts)
        ):
            raise ValueError("Vercel build output references protected source metadata")
        if is_link:
            counts["materialized_symlinks"] += 1
        return effective, is_link

    def copy_entry(path: Path, destination: Path, ancestors: frozenset[Path]) -> None:
        effective, _was_link = checked_effective_path(path)
        metadata = effective.stat()
        mode = metadata.st_mode
        if stat.S_ISREG(mode):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(effective, destination, follow_symlinks=True)
            counts["files"] += 1
            return
        if not stat.S_ISDIR(mode):
            raise ValueError("Vercel build output contains an unsupported filesystem entry")
        resolved_directory = effective.resolve(strict=True)
        if resolved_directory in ancestors:
            raise ValueError("Vercel build output contains a cyclic directory reference")
        destination.mkdir()
        counts["directories"] += 1
        next_ancestors = ancestors | {resolved_directory}
        for child in sorted(effective.iterdir(), key=lambda item: item.name):
            copy_entry(child, destination / child.name, next_ancestors)
        shutil.copystat(effective, destination, follow_symlinks=True)

    def is_allowed_vercel_python_build_file(parts: tuple[str, ...]) -> bool:
        return len(parts) >= 3 and parts[:2] == (".vercel", "python")

    def references_protected_metadata(parts: tuple[str, ...]) -> bool:
        if not parts:
            return True
        if parts[0] == ".git":
            return True
        if any(part.startswith(".env") for part in parts):
            return "/".join(parts) not in VERCEL_PUBLIC_ENV_EXAMPLE_REFERENCES
        return parts[0] == ".vercel" and not is_allowed_vercel_python_build_file(parts)

    def validated_reference(value: Any) -> tuple[str, Path]:
        if (
            not isinstance(value, str)
            or not value
            or "\\" in value
            or ":" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Vercel filePathMap contains a non-canonical reference")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.as_posix() != value
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("Vercel filePathMap contains a non-canonical reference")
        if references_protected_metadata(relative.parts):
            raise ValueError("Vercel filePathMap references protected source metadata")
        source = resolved_root.joinpath(*relative.parts)
        try:
            resolved = source.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("Vercel filePathMap contains a missing reference") from exc
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("Vercel filePathMap reference escapes the immutable source root")
        resolved_relative = resolved.relative_to(resolved_root)
        if references_protected_metadata(resolved_relative.parts):
            raise ValueError("Vercel filePathMap references protected source metadata")
        if not resolved.is_file():
            raise ValueError("Vercel filePathMap reference is not a regular file")
        return value, resolved

    try:
        (staging / ".vercel").mkdir(parents=True)
        materialized_output = staging / ".vercel" / "output"
        copy_entry(resolved_output, materialized_output, frozenset())
        if not (materialized_output / "config.json").is_file():
            raise ValueError("materialized Vercel build output has no config.json")

        project_file = resolved_root / ".vercel" / "project.json"
        if project_file.is_symlink() or not project_file.is_file():
            raise ValueError("Vercel project binding does not exist")
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Vercel project binding is invalid") from exc
        if (
            not isinstance(project, dict)
            or set(project) - {"projectId", "orgId", "projectName", "settings"}
            or not re.fullmatch(r"prj_[A-Za-z0-9]+", str(project.get("projectId") or ""))
            or not re.fullmatch(
                r"(?:team|user)_[A-Za-z0-9]+",
                str(project.get("orgId") or ""),
            )
            or project.get("projectId") != expected_project_id
            or project.get("orgId") != expected_org_id
            or not isinstance(project.get("projectName"), str)
            or not project["projectName"]
            or (
                "settings" in project
                and not isinstance(project.get("settings"), dict)
            )
        ):
            raise ValueError("Vercel project binding has an unexpected shape")
        shutil.copy2(project_file, staging / ".vercel" / "project.json")
        counts["files"] += 1

        references: dict[str, Path] = {}
        for config_file in sorted(materialized_output.rglob(".vc-config.json")):
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("Vercel function config is invalid") from exc
            if not isinstance(config, dict):
                raise ValueError("Vercel function config must be an object")
            file_path_map = config.get("filePathMap")
            if file_path_map is None:
                continue
            if not isinstance(file_path_map, dict):
                raise ValueError("Vercel function filePathMap must be an object")
            for logical_path, reference in file_path_map.items():
                if not isinstance(logical_path, str) or not logical_path:
                    raise ValueError("Vercel function filePathMap has an invalid key")
                counts["reference_declarations"] += 1
                relative, resolved = validated_reference(reference)
                existing = references.get(relative)
                if existing is not None and existing != resolved:
                    raise ValueError("Vercel filePathMap reference changed during materialization")
                references[relative] = resolved

        for relative, source in sorted(references.items()):
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=True)
            counts["files"] += 1
            counts["referenced_files"] += 1

        if any(entry.is_symlink() for entry in staging.rglob("*")):
            raise ValueError("materialized Vercel deploy root still contains a symlink")
        manifest_sha256 = _directory_sha256(staging)
        staging.replace(destination_root)
    except Exception:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise

    return {
        "schema_version": "vowpic.vercel-deploy-root-materialization.v2",
        "passed": True,
        "source_sha": source_sha,
        "runner_sha": runner_sha,
        "workflow_run_id": str(workflow_run_id),
        "workflow_attempt": workflow_attempt,
        "manifest_scope": "deploy-root",
        "manifest_sha256": manifest_sha256,
        **counts,
    }


def _read_manifest_sidecar(path: Path) -> str:
    raw = path.read_bytes()
    if not re.fullmatch(rb"[0-9a-f]{64}\n", raw):
        raise ValueError("build manifest sidecar must contain one lowercase SHA-256 line")
    return raw[:-1].decode("ascii")


def _sha256_value(value: Any, *, name: str) -> str:
    clean = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", clean):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return clean


def validate_reservation_evidence(
    inventory: dict[str, Any],
    restore: dict[str, Any],
) -> None:
    inventory_report = ProductionInventoryReport.model_validate(inventory)
    if inventory_report.schema_revision != OLD_SCHEMA:
        raise ValueError("reservation inventory must describe the exact pre-safe-baseline schema")
    if restore.get("passed") is not True:
        raise ValueError("backup/restore rehearsal is not a PASS report")
    _sha256_value(restore.get("archive_sha256"), name="restore archive hash")
    source_read_only = restore.get("source_read_only")
    if not isinstance(source_read_only, dict):
        raise ValueError("restore source read-only proof is missing")
    validate_read_only_proof(source_read_only)
    comparison = restore.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("matches") is not True:
        raise ValueError("restored database does not match the source snapshot")
    if comparison.get("schema_revision") != OLD_SCHEMA:
        raise ValueError("restore rehearsal must describe the exact pre-safe-baseline schema")
    if int(comparison.get("table_count", 0)) < 1:
        raise ValueError("restore rehearsal contains no tables")
    row_counts = comparison.get("row_counts")
    if not isinstance(row_counts, dict) or not row_counts:
        raise ValueError("restore rehearsal row counts are missing")
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in row_counts.values()):
        raise ValueError("restore rehearsal row counts are invalid")
    for field in ("row_counts_sha256", "url_inventory_sha256"):
        _sha256_value(comparison.get(field), name=f"restore {field}")
    for field in ("fk_orphans", "ledger_mismatch_users"):
        value = comparison.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"restore {field} is invalid")
    cleanup = restore.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("database_dropped") is not True or cleanup.get("role_dropped") is not True:
        raise ValueError("restore rehearsal did not prove disposable database and role cleanup")


def _evidence_pair_sha256(inventory_report: Path, restore_report: Path) -> str:
    inventory = json.loads(inventory_report.read_text(encoding="utf-8"))
    restore = json.loads(restore_report.read_text(encoding="utf-8"))
    validate_reservation_evidence(inventory, restore)
    evidence = {
        "inventory_sha256": _file_sha256(inventory_report),
        "restore_sha256": _file_sha256(restore_report),
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sync_database_url(value: str) -> str:
    clean = value.strip()
    if clean.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + clean.removeprefix("postgresql+asyncpg://")
    if clean.startswith("postgres://"):
        return "postgresql+psycopg2://" + clean.removeprefix("postgres://")
    if clean.startswith("postgresql://"):
        return "postgresql+psycopg2://" + clean.removeprefix("postgresql://")
    if clean.startswith("postgresql+psycopg2://"):
        return clean
    raise ValueError("safe-baseline database URL must use PostgreSQL")


def _current_revision(connection) -> str:
    value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    return str(value or "unknown")


def _database_identity(connection) -> tuple[str, str, str]:
    row = connection.execute(
        text(
            """
            SELECT system_identifier::text,
                   current_database(),
                   (SELECT oid::text FROM pg_database WHERE datname = current_database())
            FROM pg_control_system()
            """
        )
    ).one()
    return str(row[0]), str(row[1]), str(row[2])


def validate_database_identity(
    inventory_identity: tuple[str, str, str],
    migration_identity: tuple[str, str, str],
) -> None:
    if len(inventory_identity) != 3 or len(migration_identity) != 3:
        raise ValueError("database identity proof is incomplete")
    if any(not str(value).strip() for value in (*inventory_identity, *migration_identity)):
        raise ValueError("database identity proof contains an empty coordinate")
    if inventory_identity != migration_identity:
        raise ValueError("read-only inventory and migration URLs target different PostgreSQL databases")


def _read_only_database_identity(database_url: str) -> tuple[str, str, str]:
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                transaction_read_only = str(
                    connection.execute(text("SHOW transaction_read_only")).scalar_one()
                ).lower()
                default_read_only = str(
                    connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
                ).lower()
                if transaction_read_only != "on" or default_read_only != "on":
                    raise SafeBaselineRegistrationError(
                        "inventory database identity must be read through the protected read-only role"
                    )
                return _database_identity(connection)
    finally:
        engine.dispose()


def _read_activation(connection, *, for_update: bool = False) -> dict[str, Any] | None:
    table_exists = connection.execute(text("SELECT to_regclass('public.release_activations')")).scalar_one()
    if table_exists is None:
        return None
    suffix = " FOR UPDATE" if for_update else ""
    rows = connection.execute(
        text(
            """
            SELECT id::text, source_sha, workflow_run_id, workflow_attempt, phase,
                   phase_rank, version, reservation_expires_at, runtime_bundle_id,
                   manifest_sha256, build_artifact_id, build_artifact_digest,
                   report_sha256, api_deployment_id,
                   api_deployment_url, api_role, worker_deployment_id, worker_role,
                   worker_image_digest, current_snapshot_hash, target_snapshot_hash,
                   private_evidence_prefix, approval, acceptance_fault_intent_id,
                   acceptance_fault_intent_sha256, acceptance_fault_state,
                   acceptance_fault_expires_at, acceptance_fault_cleanup_claim_id,
                   acceptance_fault_cleanup_fencing_token, created_at
            FROM release_activations
            WHERE environment = 'production' AND kind = 'SAFE_BASELINE_INSTALL'
            """ + suffix
        )
    ).mappings().all()
    if len(rows) > 1:
        raise SafeBaselineRegistrationError("multiple Production SAFE_BASELINE_INSTALL rows exist")
    return dict(rows[0]) if rows else None


def _alembic_upgrade_on_connection(connection) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["connection"] = connection
    command.upgrade(config, TARGET_SCHEMA)


def _advisory_lock(connection) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(1448037459, 1)"))


def _configure_migration_timeouts(connection) -> None:
    connection.execute(text(f"SET LOCAL lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'"))
    connection.execute(
        text(f"SET LOCAL statement_timeout = '{MIGRATION_STATEMENT_TIMEOUT}'")
    )


def _preflight(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    runner_sha: str | None = None,
) -> dict[str, Any]:
    effective_runner_sha = runner_sha or source_sha
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                transaction_read_only = str(
                    connection.execute(text("SHOW transaction_read_only")).scalar_one()
                ).lower() == "on"
                default_read_only = str(
                    connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
                ).lower() == "on"
                if not transaction_read_only or not default_read_only:
                    raise SafeBaselineRegistrationError("preflight role and transaction must both be read-only")
                revision = _current_revision(connection)
                activation = _read_activation(connection)
        state = classify_install_state(
            current_revision=revision,
            activation=activation,
            source_sha=source_sha,
            workflow_run_id=workflow_run_id,
        )
        if activation is not None and state in RETRIABLE_STATES:
            validate_resume_coordinates(
                activation,
                source_sha=source_sha,
                workflow_run_id=workflow_run_id,
                workflow_attempt=workflow_attempt,
            )
        if state == "TAKEOVER_RESERVED_BUILD":
            if activation is None or effective_runner_sha != source_sha:
                raise SafeBaselineRegistrationError(
                    "reserved build repair must run and build the same reviewed source"
                )
            validate_reserved_build_repair_descendant(
                str(activation.get("source_sha") or ""),
                source_sha,
            )
        if state == "TAKEOVER_STAGED":
            validate_staged_control_descendant(source_sha, effective_runner_sha)
        if state not in {
            "FRESH_INSTALL",
            "TAKEOVER_RESERVED",
            "TAKEOVER_RESERVED_BUILD",
            "TAKEOVER_STAGED",
            *RETRIABLE_STATES,
        }:
            raise SafeBaselineRegistrationError(f"safe-baseline preflight rejected state: {state}")
        if state == "RETRY_FORMAL_VERIFIED":
            parse_reference(str(activation.get("private_evidence_prefix") or ""))
            _validate_sha(
                str(activation.get("report_sha256") or ""),
                name="stored formal report SHA-256",
            )
        return {
            "action": "preflight",
            "state": state,
            "schema_revision": revision,
            "activation_phase": activation.get("phase") if activation else None,
            "activation": {
                key: activation.get(key)
                for key in (
                    "runtime_bundle_id",
                    "manifest_sha256",
                    "build_artifact_id",
                    "build_artifact_digest",
                    "source_sha",
                    "workflow_run_id",
                    "workflow_attempt",
                    "api_deployment_id",
                    "api_deployment_url",
                    "api_role",
                    "current_snapshot_hash",
                    "private_evidence_prefix",
                    "report_sha256",
                    "phase",
                    "version",
                )
            } if activation else None,
            "source_sha": source_sha,
            "runner_sha": effective_runner_sha,
            "workflow_run_id": workflow_run_id,
            "reservation_expired": reservation_is_expired(activation),
            "build_artifact_recovery_expired": build_artifact_recovery_is_expired(
                activation
            ),
        }
    finally:
        engine.dispose()


def _adopt_reserved(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    expected_source_sha: str,
    expected_workflow_run_id: str,
    expected_version: int,
    approval: str,
    evidence_prefix: str,
) -> dict[str, Any]:
    expected_source_sha = _validate_sha(
        expected_source_sha,
        name="reserving source SHA",
        lengths=(40, 64),
    )
    if (
        not re.fullmatch(r"[1-9][0-9]*", expected_workflow_run_id)
        or not re.fullmatch(r"[1-9][0-9]*", workflow_run_id)
        or expected_version < 1
    ):
        raise ValueError("current/reserving workflow run IDs and positive version are required")
    if not approval or len(approval) > 160:
        raise ValueError("protected reservation approval is required")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
        r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
        evidence_prefix,
    ):
        raise ValueError("reservation adoption requires one durable GitHub artifact URL")
    validate_descendant_source(expected_source_sha, source_sha)

    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("reservation adoption requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            if (
                str(activation.get("source_sha") or "") != expected_source_sha
                or str(activation.get("workflow_run_id") or "") != expected_workflow_run_id
                or int(activation.get("version") or 0) != expected_version
            ):
                raise SafeBaselineRegistrationError("reservation adoption coordinates changed")
            if str(activation.get("approval") or "") != approval:
                raise SafeBaselineRegistrationError("reservation adoption approval does not match")
            if not reserved_install_is_adoptable(activation):
                raise SafeBaselineRegistrationError("only an unbound RESERVED install can be adopted")
            previous_evidence_prefix = str(activation.get("private_evidence_prefix") or "")
            result = connection.execute(
                text(
                    """
                    UPDATE release_activations
                    SET source_sha = :source_sha,
                        workflow_run_id = :workflow_run_id,
                        workflow_attempt = :workflow_attempt,
                        private_evidence_prefix = :evidence_prefix,
                        version = version + 1
                    WHERE id = CAST(:activation_id AS uuid)
                      AND version = :expected_version
                      AND phase = 'RESERVED'
                      AND source_sha = :expected_source_sha
                      AND workflow_run_id = :expected_workflow_run_id
                      AND approval = :approval
                      AND runtime_bundle_id IS NULL
                      AND manifest_sha256 IS NULL
                      AND build_artifact_id IS NULL
                      AND build_artifact_digest IS NULL
                      AND report_sha256 IS NULL
                      AND api_deployment_id IS NULL
                      AND api_deployment_url IS NULL
                      AND api_role IS NULL
                      AND worker_deployment_id IS NULL
                      AND worker_role IS NULL
                      AND worker_image_digest IS NULL
                      AND target_snapshot_hash IS NULL
                      AND acceptance_fault_intent_id IS NULL
                      AND acceptance_fault_intent_sha256 IS NULL
                      AND acceptance_fault_state IS NULL
                      AND acceptance_fault_expires_at IS NULL
                      AND acceptance_fault_cleanup_claim_id IS NULL
                      AND acceptance_fault_cleanup_fencing_token IS NULL
                    RETURNING id::text, version
                    """
                ),
                {
                    "source_sha": source_sha,
                    "workflow_run_id": workflow_run_id,
                    "workflow_attempt": workflow_attempt,
                    "evidence_prefix": evidence_prefix,
                    "activation_id": activation["id"],
                    "expected_version": expected_version,
                    "expected_source_sha": expected_source_sha,
                    "expected_workflow_run_id": expected_workflow_run_id,
                    "approval": approval,
                },
            ).mappings().one_or_none()
            if result is None:
                raise SafeBaselineRegistrationError("reservation adoption CAS lost")
        return {
            "action": "adopt-reserved",
            "state": "RESERVATION_ADOPTED",
            "activation_id": result["id"],
            "version": result["version"],
            "previous_source_sha": expected_source_sha,
            "previous_workflow_run_id": expected_workflow_run_id,
            "previous_evidence_prefix": previous_evidence_prefix,
            "source_sha": source_sha,
            "workflow_run_id": workflow_run_id,
            "workflow_attempt": workflow_attempt,
            "evidence_prefix": evidence_prefix,
        }
    finally:
        engine.dispose()


def _adopt_staged_verifier(
    database_url: str,
    *,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    expected_source_sha: str,
    expected_workflow_run_id: str,
    expected_version: int,
    approval: str,
    evidence_prefix: str,
) -> dict[str, Any]:
    source_sha = _validate_sha(source_sha, name="deployed source SHA", lengths=(40, 64))
    runner_sha = _validate_sha(runner_sha, name="reviewed runner SHA", lengths=(40, 64))
    expected_source_sha = _validate_sha(
        expected_source_sha,
        name="recorded deployed source SHA",
        lengths=(40, 64),
    )
    if source_sha != expected_source_sha:
        raise SafeBaselineRegistrationError(
            "staged verifier takeover cannot change the deployed source"
        )
    if (
        not re.fullmatch(r"[1-9][0-9]*", expected_workflow_run_id)
        or not re.fullmatch(r"[1-9][0-9]*", workflow_run_id)
        or workflow_attempt < 1
        or expected_version < 1
    ):
        raise ValueError("current/recorded workflow coordinates and positive version are required")
    if not approval or len(approval) > 160:
        raise ValueError("protected staged-verifier approval is required")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
        r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
        evidence_prefix,
    ):
        raise ValueError("staged verifier takeover requires one durable GitHub artifact URL")
    validate_staged_control_descendant(source_sha, runner_sha)

    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("staged verifier takeover requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            if (
                str(activation.get("source_sha") or "") != source_sha
                or str(activation.get("workflow_run_id") or "") != expected_workflow_run_id
                or int(activation.get("version") or 0) != expected_version
            ):
                raise SafeBaselineRegistrationError("staged verifier takeover coordinates changed")
            if str(activation.get("approval") or "") != approval:
                raise SafeBaselineRegistrationError("staged verifier takeover approval does not match")
            if not staged_verifier_takeover_is_adoptable(activation):
                raise SafeBaselineRegistrationError(
                    "only a fully bound, unpromoted STAGED install can be adopted"
                )
            previous_evidence_prefix = str(activation.get("private_evidence_prefix") or "")
            result = connection.execute(
                text(
                    """
                    UPDATE release_activations
                    SET workflow_run_id = :workflow_run_id,
                        workflow_attempt = :workflow_attempt,
                        private_evidence_prefix = :evidence_prefix,
                        version = version + 1
                    WHERE id = CAST(:activation_id AS uuid)
                      AND version = :expected_version
                      AND phase = 'STAGED'
                      AND source_sha = :source_sha
                      AND workflow_run_id = :expected_workflow_run_id
                      AND approval = :approval
                      AND runtime_bundle_id IS NOT NULL
                      AND manifest_sha256 IS NOT NULL
                      AND build_artifact_id IS NOT NULL
                      AND build_artifact_digest IS NOT NULL
                      AND report_sha256 IS NULL
                      AND api_deployment_id IS NOT NULL
                      AND api_deployment_url IS NOT NULL
                      AND api_role = 'SAFE_BASELINE'
                      AND worker_deployment_id IS NULL
                      AND worker_role IS NULL
                      AND worker_image_digest IS NULL
                      AND target_snapshot_hash IS NULL
                      AND acceptance_fault_intent_id IS NULL
                      AND acceptance_fault_intent_sha256 IS NULL
                      AND acceptance_fault_state IS NULL
                      AND acceptance_fault_expires_at IS NULL
                      AND acceptance_fault_cleanup_claim_id IS NULL
                      AND acceptance_fault_cleanup_fencing_token IS NULL
                    RETURNING id::text, version
                    """
                ),
                {
                    "workflow_run_id": workflow_run_id,
                    "workflow_attempt": workflow_attempt,
                    "evidence_prefix": evidence_prefix,
                    "activation_id": activation["id"],
                    "expected_version": expected_version,
                    "source_sha": source_sha,
                    "expected_workflow_run_id": expected_workflow_run_id,
                    "approval": approval,
                },
            ).mappings().one_or_none()
            if result is None:
                raise SafeBaselineRegistrationError("staged verifier takeover CAS lost")
        return {
            "action": "adopt-staged-verifier",
            "state": "STAGED_VERIFIER_ADOPTED",
            "activation_id": result["id"],
            "version": result["version"],
            "previous_workflow_run_id": expected_workflow_run_id,
            "previous_evidence_prefix": previous_evidence_prefix,
            "source_sha": source_sha,
            "runner_sha": runner_sha,
            "workflow_run_id": workflow_run_id,
            "workflow_attempt": workflow_attempt,
            "evidence_prefix": evidence_prefix,
        }
    finally:
        engine.dispose()


def verify_sensitive_runtime_secret_proof(
    vercel_env_file: Path,
    *,
    secret_name: str,
    fingerprint_name: str,
    expected_secret: str,
    vercel_token: str,
    project_id: str,
    team_id: str,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> dict[str, Any]:
    """Prove one unreadable Vercel Sensitive value has the protected rotation."""
    if secret_name != "ACCEPTANCE_IDENTITY_HMAC_KEY":
        raise ValueError("only the acceptance identity runtime secret can be verified")
    if fingerprint_name != "ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256":
        raise ValueError("only the acceptance identity runtime fingerprint can be verified")
    expected = str(expected_secret or "").strip()
    if len(expected) < 32:
        raise ValueError("protected acceptance identity secret must contain at least 32 characters")
    if (
        not vercel_token.strip()
        or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", project_id) is None
        or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", team_id) is None
    ):
        raise ValueError("protected Vercel project coordinates are incomplete")
    if vercel_env_file.is_symlink() or not vercel_env_file.is_file():
        raise ValueError("pulled Vercel Production environment file is missing")
    pulled_fingerprint = str(
        dotenv_values(vercel_env_file).get(fingerprint_name) or ""
    ).strip()
    if re.fullmatch(r"[0-9a-f]{64}", pulled_fingerprint) is None:
        raise ValueError("Vercel Production acceptance identity fingerprint is missing or invalid")
    expected_fingerprint = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(pulled_fingerprint, expected_fingerprint):
        raise ValueError("GitHub secret and Vercel Production fingerprint do not match")

    response = httpx.get(
        f"https://api.vercel.com/v10/projects/{quote(project_id, safe='')}/env",
        params={"teamId": team_id, "target": "production"},
        headers={"Authorization": f"Bearer {vercel_token}"},
        timeout=30.0,
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("envs"), list):
        records = payload["envs"]
    else:
        raise ValueError("Vercel Production environment metadata is malformed")
    matches = [
        record
        for record in records
        if isinstance(record, dict) and record.get("key") == secret_name
    ]
    if len(matches) != 1:
        raise ValueError("Vercel Production acceptance identity secret metadata is not unique")
    secret_record = matches[0]
    record_id = str(secret_record.get("id") or secret_record.get("uid") or "").strip()
    targets = secret_record.get("target")
    if isinstance(targets, str):
        targets = [targets]
    custom_environment_ids = secret_record.get("customEnvironmentIds")
    if custom_environment_ids is None:
        custom_environment_ids = []
    if (
        re.fullmatch(r"[A-Za-z0-9_-]{1,200}", record_id) is None
        or secret_record.get("type") != "sensitive"
        or not isinstance(targets, list)
        or any(not isinstance(target, str) for target in targets)
        or targets != ["production"]
        or secret_record.get("gitBranch") not in (None, "")
        or not isinstance(custom_environment_ids, list)
        or custom_environment_ids
        or secret_record.get("decrypted") is True
    ):
        raise ValueError(
            "Vercel acceptance identity secret is not Sensitive and Production-only"
        )
    if (
        re.fullmatch(r"[0-9a-f]{40,64}", source_sha) is None
        or re.fullmatch(r"[0-9a-f]{40,64}", runner_sha) is None
        or re.fullmatch(r"[1-9][0-9]*", workflow_run_id) is None
        or workflow_attempt < 1
    ):
        raise ValueError("runtime secret evidence coordinates are invalid")
    return {
        "schema_version": "vowpic.runtime-secret-control-proof.v1",
        "passed": True,
        "secret_name": secret_name,
        "fingerprint_name": fingerprint_name,
        "minimum_length": 32,
        "vercel_environment": "production",
        "vercel_value_readable": False,
        "vercel_secret_type": "sensitive",
        "vercel_target": ["production"],
        "project_id": project_id,
        "team_id": team_id,
        "source_sha": source_sha,
        "runner_sha": runner_sha,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
    }


def _validate_runtime_secret_evidence(
    path: Path,
    *,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    project_id: str,
    team_id: str,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("staged rearm requires runtime secret control evidence")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": "vowpic.runtime-secret-control-proof.v1",
        "passed": True,
        "secret_name": "ACCEPTANCE_IDENTITY_HMAC_KEY",
        "fingerprint_name": "ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
        "minimum_length": 32,
        "vercel_environment": "production",
        "vercel_value_readable": False,
        "vercel_secret_type": "sensitive",
        "vercel_target": ["production"],
        "project_id": project_id,
        "team_id": team_id,
        "source_sha": source_sha,
        "runner_sha": runner_sha,
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("runtime secret evidence is not bound to this protected rearm")
    if set(payload) != set(expected):
        raise ValueError("runtime secret evidence contains unexpected fields")
    return payload


def _rearm_invalid_reserved_build(
    database_url: str,
    *,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    expected_source_sha: str,
    expected_workflow_run_id: str,
    expected_version: int,
    expected_build_artifact_id: str,
    expected_build_artifact_digest: str,
    approval: str,
    evidence_prefix: str,
) -> dict[str, Any]:
    """Clear one invalid bound prebuild only when no deployment was created."""
    source_sha = _validate_sha(source_sha, name="replacement source SHA", lengths=(40, 64))
    runner_sha = _validate_sha(runner_sha, name="reviewed runner SHA", lengths=(40, 64))
    expected_source_sha = _validate_sha(
        expected_source_sha,
        name="recorded bound source SHA",
        lengths=(40, 64),
    )
    expected_build_artifact_id = str(expected_build_artifact_id or "").strip()
    expected_build_artifact_digest = str(expected_build_artifact_digest or "").strip().lower()
    if runner_sha != source_sha:
        raise SafeBaselineRegistrationError(
            "reserved build rearm must run and build the same reviewed source"
        )
    if (
        not re.fullmatch(r"[1-9][0-9]*", expected_workflow_run_id)
        or not re.fullmatch(r"[1-9][0-9]*", workflow_run_id)
        or workflow_attempt < 1
        or expected_version < 1
        or not re.fullmatch(r"[1-9][0-9]{0,19}", expected_build_artifact_id)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_build_artifact_digest)
    ):
        raise ValueError("current and recorded RESERVED build coordinates are required")
    if not approval or len(approval) > 160:
        raise ValueError("protected reserved-build rearm approval is required")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
        r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
        evidence_prefix,
    ):
        raise ValueError("reserved build rearm requires one durable GitHub artifact URL")
    validate_reserved_build_repair_descendant(expected_source_sha, source_sha)

    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _configure_migration_timeouts(connection)
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("reserved build rearm requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            if (
                str(activation.get("source_sha") or "") != expected_source_sha
                or str(activation.get("workflow_run_id") or "") != expected_workflow_run_id
                or int(activation.get("version") or 0) != expected_version
                or str(activation.get("build_artifact_id") or "")
                != expected_build_artifact_id
                or str(activation.get("build_artifact_digest") or "").lower()
                != expected_build_artifact_digest
            ):
                raise SafeBaselineRegistrationError("reserved build rearm coordinates changed")
            if str(activation.get("approval") or "") != approval:
                raise SafeBaselineRegistrationError("reserved build rearm approval does not match")
            if not reserved_build_rearm_is_adoptable(activation):
                raise SafeBaselineRegistrationError(
                    "only a bound, undeployed RESERVED prebuild can be rearmed"
                )

            previous_coordinates = {
                key: activation.get(key)
                for key in (
                    "source_sha",
                    "workflow_run_id",
                    "workflow_attempt",
                    "version",
                    "manifest_sha256",
                    "build_artifact_id",
                    "build_artifact_digest",
                    "private_evidence_prefix",
                )
            }
            previous_coordinates_sha256 = hashlib.sha256(
                json.dumps(
                    previous_coordinates,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            next_workflow_attempt = workflow_attempt + 1

            # The regression trigger makes bound build coordinates immutable.
            # ACCESS EXCLUSIVE keeps this one reviewed repair atomic while the
            # trigger is disabled inside the same transaction.
            connection.execute(
                text(
                    "ALTER TABLE release_activations "
                    "DISABLE TRIGGER trg_release_activation_regression"
                )
            )
            try:
                result = connection.execute(
                    text(
                        """
                        UPDATE release_activations
                        SET source_sha = :source_sha,
                            workflow_run_id = :workflow_run_id,
                            workflow_attempt = :next_workflow_attempt,
                            manifest_sha256 = NULL,
                            build_artifact_id = NULL,
                            build_artifact_digest = NULL,
                            private_evidence_prefix = :evidence_prefix,
                            updated_at = CURRENT_TIMESTAMP,
                            version = version + 1
                        WHERE id = CAST(:activation_id AS uuid)
                          AND version = :expected_version
                          AND phase = 'RESERVED'
                          AND source_sha = :expected_source_sha
                          AND workflow_run_id = :expected_workflow_run_id
                          AND approval = :approval
                          AND runtime_bundle_id IS NULL
                          AND manifest_sha256 IS NOT NULL
                          AND build_artifact_id = :expected_build_artifact_id
                          AND build_artifact_digest = :expected_build_artifact_digest
                          AND report_sha256 IS NULL
                          AND api_deployment_id IS NULL
                          AND api_deployment_url IS NULL
                          AND api_role IS NULL
                          AND worker_deployment_id IS NULL
                          AND worker_role IS NULL
                          AND worker_image_digest IS NULL
                          AND target_snapshot_hash IS NULL
                          AND acceptance_fault_intent_id IS NULL
                          AND acceptance_fault_intent_sha256 IS NULL
                          AND acceptance_fault_state IS NULL
                          AND acceptance_fault_expires_at IS NULL
                          AND acceptance_fault_cleanup_claim_id IS NULL
                          AND acceptance_fault_cleanup_fencing_token IS NULL
                        RETURNING id::text, version
                        """
                    ),
                    {
                        "workflow_run_id": workflow_run_id,
                        "next_workflow_attempt": next_workflow_attempt,
                        "evidence_prefix": evidence_prefix,
                        "activation_id": activation["id"],
                        "expected_version": expected_version,
                        "source_sha": source_sha,
                        "expected_source_sha": expected_source_sha,
                        "expected_workflow_run_id": expected_workflow_run_id,
                        "expected_build_artifact_id": expected_build_artifact_id,
                        "expected_build_artifact_digest": expected_build_artifact_digest,
                        "approval": approval,
                    },
                ).mappings().one_or_none()
            finally:
                connection.execute(
                    text(
                        "ALTER TABLE release_activations "
                        "ENABLE TRIGGER trg_release_activation_regression"
                    )
                )
            if result is None:
                raise SafeBaselineRegistrationError("reserved build rearm CAS lost")
        return {
            "action": "rearm-reserved-build",
            "state": "RESERVED_BUILD_REARMED",
            "activation_id": result["id"],
            "version": result["version"],
            "source_sha": source_sha,
            "previous_source_sha": expected_source_sha,
            "runner_sha": runner_sha,
            "workflow_run_id": workflow_run_id,
            "workflow_attempt": workflow_attempt,
            "next_workflow_attempt": next_workflow_attempt,
            "previous_coordinates_sha256": previous_coordinates_sha256,
            "previous_evidence_prefix": previous_coordinates["private_evidence_prefix"],
            "evidence_prefix": evidence_prefix,
        }
    finally:
        engine.dispose()


def _rearm_invalid_staged(
    database_url: str,
    *,
    source_sha: str,
    runner_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    expected_source_sha: str,
    expected_workflow_run_id: str,
    expected_version: int,
    approval: str,
    evidence_prefix: str,
    runtime_secret_evidence: Path,
    project_id: str,
    team_id: str,
) -> dict[str, Any]:
    """Atomically clear one invalid, unpromoted STAGED binding for an exact-source rebuild."""
    source_sha = _validate_sha(source_sha, name="deployed source SHA", lengths=(40, 64))
    runner_sha = _validate_sha(runner_sha, name="reviewed runner SHA", lengths=(40, 64))
    expected_source_sha = _validate_sha(
        expected_source_sha,
        name="recorded deployed source SHA",
        lengths=(40, 64),
    )
    if source_sha != expected_source_sha:
        raise SafeBaselineRegistrationError("staged rearm cannot change the deployed source")
    if (
        not re.fullmatch(r"[1-9][0-9]*", expected_workflow_run_id)
        or not re.fullmatch(r"[1-9][0-9]*", workflow_run_id)
        or workflow_attempt < 1
        or expected_version < 1
    ):
        raise ValueError("current/recorded workflow coordinates and positive version are required")
    if not approval or len(approval) > 160:
        raise ValueError("protected staged-rearm approval is required")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/"
        r"[1-9][0-9]*/artifacts/[1-9][0-9]*",
        evidence_prefix,
    ):
        raise ValueError("staged rearm requires one durable GitHub artifact URL")
    validate_staged_control_descendant(source_sha, runner_sha)
    _validate_runtime_secret_evidence(
        runtime_secret_evidence,
        source_sha=source_sha,
        runner_sha=runner_sha,
        workflow_run_id=workflow_run_id,
        workflow_attempt=workflow_attempt,
        project_id=project_id,
        team_id=team_id,
    )

    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _configure_migration_timeouts(connection)
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("staged rearm requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            if (
                str(activation.get("source_sha") or "") != source_sha
                or str(activation.get("workflow_run_id") or "") != expected_workflow_run_id
                or int(activation.get("version") or 0) != expected_version
            ):
                raise SafeBaselineRegistrationError("staged rearm coordinates changed")
            if str(activation.get("approval") or "") != approval:
                raise SafeBaselineRegistrationError("staged rearm approval does not match")
            if not staged_verifier_takeover_is_adoptable(activation):
                raise SafeBaselineRegistrationError(
                    "only a fully bound, unpromoted STAGED install can be rearmed"
                )

            previous_coordinates = {
                key: activation.get(key)
                for key in (
                    "source_sha",
                    "workflow_run_id",
                    "workflow_attempt",
                    "version",
                    "runtime_bundle_id",
                    "manifest_sha256",
                    "build_artifact_id",
                    "build_artifact_digest",
                    "api_deployment_id",
                    "api_deployment_url",
                    "private_evidence_prefix",
                )
            }
            previous_coordinates_sha256 = hashlib.sha256(
                json.dumps(
                    previous_coordinates,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            next_workflow_attempt = workflow_attempt + 1

            # ACCESS EXCLUSIVE is held by ALTER TABLE until commit, so no other
            # session can mutate the row while its regression trigger is disabled.
            connection.execute(
                text(
                    "ALTER TABLE release_activations "
                    "DISABLE TRIGGER trg_release_activation_regression"
                )
            )
            try:
                result = connection.execute(
                    text(
                        """
                        UPDATE release_activations
                        SET workflow_run_id = :workflow_run_id,
                            workflow_attempt = :next_workflow_attempt,
                            phase = 'RESERVED',
                            phase_rank = 0,
                            runtime_bundle_id = NULL,
                            manifest_sha256 = NULL,
                            build_artifact_id = NULL,
                            build_artifact_digest = NULL,
                            report_sha256 = NULL,
                            api_deployment_id = NULL,
                            api_deployment_url = NULL,
                            api_role = NULL,
                            worker_deployment_id = NULL,
                            worker_role = NULL,
                            worker_image_digest = NULL,
                            target_snapshot_hash = NULL,
                            private_evidence_prefix = :evidence_prefix,
                            updated_at = CURRENT_TIMESTAMP,
                            version = version + 1
                        WHERE id = CAST(:activation_id AS uuid)
                          AND version = :expected_version
                          AND phase = 'STAGED'
                          AND source_sha = :source_sha
                          AND workflow_run_id = :expected_workflow_run_id
                          AND approval = :approval
                          AND runtime_bundle_id IS NOT NULL
                          AND manifest_sha256 IS NOT NULL
                          AND build_artifact_id IS NOT NULL
                          AND build_artifact_digest IS NOT NULL
                          AND report_sha256 IS NULL
                          AND api_deployment_id IS NOT NULL
                          AND api_deployment_url IS NOT NULL
                          AND api_role = 'SAFE_BASELINE'
                          AND worker_deployment_id IS NULL
                          AND worker_role IS NULL
                          AND worker_image_digest IS NULL
                          AND target_snapshot_hash IS NULL
                          AND acceptance_fault_intent_id IS NULL
                          AND acceptance_fault_intent_sha256 IS NULL
                          AND acceptance_fault_state IS NULL
                          AND acceptance_fault_expires_at IS NULL
                          AND acceptance_fault_cleanup_claim_id IS NULL
                          AND acceptance_fault_cleanup_fencing_token IS NULL
                        RETURNING id::text, version
                        """
                    ),
                    {
                        "workflow_run_id": workflow_run_id,
                        "next_workflow_attempt": next_workflow_attempt,
                        "evidence_prefix": evidence_prefix,
                        "activation_id": activation["id"],
                        "expected_version": expected_version,
                        "source_sha": source_sha,
                        "expected_workflow_run_id": expected_workflow_run_id,
                        "approval": approval,
                    },
                ).mappings().one_or_none()
            finally:
                connection.execute(
                    text(
                        "ALTER TABLE release_activations "
                        "ENABLE TRIGGER trg_release_activation_regression"
                    )
                )
            if result is None:
                raise SafeBaselineRegistrationError("staged rearm CAS lost")
        return {
            "action": "rearm-staged",
            "state": "STAGED_REARMED",
            "activation_id": result["id"],
            "version": result["version"],
            "source_sha": source_sha,
            "runner_sha": runner_sha,
            "workflow_run_id": workflow_run_id,
            "workflow_attempt": workflow_attempt,
            "next_workflow_attempt": next_workflow_attempt,
            "previous_coordinates_sha256": previous_coordinates_sha256,
            "previous_deployment_id": previous_coordinates["api_deployment_id"],
            "previous_evidence_prefix": previous_coordinates["private_evidence_prefix"],
            "evidence_prefix": evidence_prefix,
        }
    finally:
        engine.dispose()


def _reserve(
    database_url: str,
    *,
    inventory_database_url: str,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    approval: str,
    inventory_report: Path,
    restore_report: Path,
    evidence_prefix: str,
    inject_failure: str | None,
) -> dict[str, Any]:
    evidence_hash = _evidence_pair_sha256(inventory_report, restore_report)
    inventory_database_identity = _read_only_database_identity(inventory_database_url)
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _configure_migration_timeouts(connection)
            _advisory_lock(connection)
            validate_database_identity(
                inventory_database_identity,
                _database_identity(connection),
            )
            revision = _current_revision(connection)
            activation = _read_activation(connection, for_update=revision == TARGET_SCHEMA)
            state = classify_install_state(
                current_revision=revision,
                activation=activation,
                source_sha=source_sha,
                workflow_run_id=workflow_run_id,
            )
            if state == "RETRY_RESERVED":
                validate_resume_coordinates(
                    activation,
                    source_sha=source_sha,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt=workflow_attempt,
                )
                return {"action": "reserve", "state": state, "activation_id": activation["id"]}
            if state != "FRESH_INSTALL":
                raise SafeBaselineRegistrationError(f"reservation rejected state: {state}")
            if inject_failure == "BEFORE_MIGRATION":
                raise SafeBaselineRegistrationError("injected failure before migration")
            os.environ["DATABASE_URL"] = database_url
            _alembic_upgrade_on_connection(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("Alembic did not reach the exact safe-baseline schema")
            inventory_rls = reconcile_inventory_rls_policies(connection)
            if inject_failure == "AFTER_MIGRATION_BEFORE_RESERVATION":
                raise SafeBaselineRegistrationError("injected failure after migration")
            activation_id = str(uuid.uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO release_activations (
                      id, environment, kind, source_sha, workflow_run_id, workflow_attempt,
                      phase, phase_rank, version, approval, reservation_expires_at,
                      current_snapshot_hash, private_evidence_prefix
                    ) VALUES (
                      CAST(:id AS uuid), 'production', 'SAFE_BASELINE_INSTALL', :source_sha,
                      :workflow_run_id, :workflow_attempt, 'RESERVED', 0, 1, :approval,
                      CURRENT_TIMESTAMP + make_interval(mins => :reservation_ttl_minutes),
                      :evidence_hash, :evidence_prefix
                    )
                    """
                ),
                {
                    "id": activation_id,
                    "source_sha": source_sha,
                    "workflow_run_id": workflow_run_id,
                    "workflow_attempt": workflow_attempt,
                    "approval": approval,
                    "reservation_ttl_minutes": RESERVATION_TTL_MINUTES,
                    "evidence_hash": evidence_hash,
                    "evidence_prefix": evidence_prefix,
                },
            )
            if inject_failure == "AFTER_RESERVATION_BEFORE_COMMIT":
                raise SafeBaselineRegistrationError("injected failure before reservation commit")
        return {
            "action": "reserve",
            "state": "RESERVED",
            "activation_id": activation_id,
            "schema_revision": TARGET_SCHEMA,
            "evidence_pair_sha256": evidence_hash,
            "inventory_rls": inventory_rls,
        }
    finally:
        engine.dispose()


def _advance_phase(database_url: str, args: argparse.Namespace) -> dict[str, Any]:
    target_phase = args.phase
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("safe-baseline activation requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            if activation["phase"] == "COMPLETED":
                raise SafeBaselineRegistrationError("completed safe-baseline install is immutable")
            validate_resume_coordinates(
                activation,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
            )
            reservation_expired = reservation_is_expired(activation)
            if activation["phase"] == target_phase:
                if target_phase == "FORMAL_VERIFIED":
                    raise SafeBaselineRegistrationError(
                        "FORMAL_VERIFIED evidence is immutable; verify the stored reference before completion"
                    )
                return {
                    "action": "advance",
                    "state": f"ALREADY_{target_phase}",
                    "activation_id": activation["id"],
                    "reservation_expired": reservation_is_expired(activation),
                }
            validate_phase_transition(str(activation["phase"]), target_phase)

            updates: dict[str, Any] = {
                "phase": target_phase,
                "phase_rank": PHASE_RANK[target_phase],
                "workflow_attempt": args.workflow_attempt,
            }
            if target_phase == "STAGED":
                runtime_bundle_id = str(args.runtime_bundle_id or "").strip()
                deployment_id = str(args.deployment_id or "").strip()
                deployment_url = str(args.deployment_url or "").strip()
                if not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime_bundle_id):
                    raise ValueError("STAGED requires the canonical SAFE_BASELINE runtime bundle ID")
                if not deployment_id or len(deployment_id) > 160 or any(char.isspace() for char in deployment_id):
                    raise ValueError("STAGED requires an exact Vercel deployment ID")
                if not re.fullmatch(r"https://[^\s]+", deployment_url):
                    raise ValueError("STAGED requires an HTTPS deployment URL")
                if args.manifest_sha256:
                    manifest_sha256 = _validate_sha(args.manifest_sha256, name="build manifest SHA-256")
                elif args.build_output:
                    manifest_sha256 = _directory_sha256(Path(args.build_output))
                else:
                    raise ValueError("STAGED requires --build-output or --manifest-sha256")
                if (
                    activation.get("manifest_sha256")
                    and activation["manifest_sha256"] != manifest_sha256
                ):
                    raise ValueError(
                        "STAGED manifest does not match the build bound while RESERVED"
                    )
                updates.update(
                    runtime_bundle_id=runtime_bundle_id,
                    manifest_sha256=manifest_sha256,
                    api_deployment_id=deployment_id,
                    api_deployment_url=deployment_url,
                    api_role="SAFE_BASELINE",
                )
            elif target_phase in {
                "PROMOTION_ARMED",
                "PROMOTED",
                "FORMAL_VERIFIED",
                "COMPLETED",
            }:
                if args.deployment_url and args.deployment_url != activation["api_deployment_url"]:
                    raise ValueError("deployment URL does not match the immutable STAGED activation")
                if args.deployment_id and args.deployment_id != activation["api_deployment_id"]:
                    raise ValueError("deployment ID does not match the immutable STAGED activation")
            if target_phase == "FORMAL_VERIFIED":
                report_path = Path(args.formal_report)
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                if payload.get("passed") is not True:
                    raise ValueError("formal report is not a PASS report")
                updates["report_sha256"] = _file_sha256(report_path)
                evidence_reference = str(args.evidence_prefix or "").strip()
                parse_reference(evidence_reference)
                updates["private_evidence_prefix"] = evidence_reference
                snapshot_hash = str(payload.get("after_snapshot_sha256") or "")
                updates["target_snapshot_hash"] = _validate_sha(
                    snapshot_hash,
                    name="formal after-snapshot hash",
                )

            assignments = ["phase = :phase", "phase_rank = :phase_rank", "workflow_attempt = :workflow_attempt"]
            for column in (
                "runtime_bundle_id",
                "manifest_sha256",
                "api_deployment_id",
                "api_deployment_url",
                "api_role",
                "report_sha256",
                "target_snapshot_hash",
                "private_evidence_prefix",
            ):
                if column in updates:
                    assignments.append(f"{column} = :{column}")
            updates.update(
                activation_id=activation["id"],
                expected_version=activation["version"],
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
            )
            result = connection.execute(
                text(
                    f"""
                    UPDATE release_activations
                    SET {', '.join(assignments)}, version = version + 1
                    WHERE id = CAST(:activation_id AS uuid)
                      AND version = :expected_version
                      AND source_sha = :source_sha
                      AND workflow_run_id = :workflow_run_id
                    RETURNING id::text, version
                    """
                ),
                updates,
            ).mappings().one_or_none()
            if result is None:
                raise SafeBaselineRegistrationError("safe-baseline phase CAS lost")
        return {
            "action": "advance",
            "state": target_phase,
            "activation_id": result["id"],
            "version": result["version"],
            "reservation_expired": reservation_expired,
        }
    finally:
        engine.dispose()


def _required_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _vercel_deployment_url(value: Any) -> str | None:
    clean = str(value or "").strip().lower()
    parsed = urlsplit(clean if "://" in clean else f"https://{clean}")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
        or not parsed.hostname.endswith(".vercel.app")
    ):
        return None
    return f"https://{parsed.hostname}"


def _bind_build_manifest(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    build_artifact_attempt: int,
    build_artifact_id: str,
    build_artifact_digest: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    manifest_sha256 = _validate_sha(
        manifest_sha256,
        name="build manifest SHA-256",
    )
    if build_artifact_attempt < 1 or build_artifact_attempt > workflow_attempt:
        raise ValueError(
            "build artifact attempt must be positive and no newer than the caller"
        )
    clean_artifact_id = str(build_artifact_id or "").strip()
    clean_artifact_digest = str(build_artifact_digest or "").strip().lower()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", clean_artifact_id):
        raise ValueError("build artifact ID must be a positive decimal ID")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", clean_artifact_digest):
        raise ValueError("build artifact digest must be sha256:<64 lowercase hex>")
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _advisory_lock(connection)
            if _current_revision(connection) != TARGET_SCHEMA:
                raise SafeBaselineRegistrationError("build binding requires schema 0014")
            activation = _read_activation(connection, for_update=True)
            if activation is None:
                raise SafeBaselineRegistrationError("safe-baseline activation row is missing")
            state = classify_install_state(
                current_revision=TARGET_SCHEMA,
                activation=activation,
                source_sha=source_sha,
                workflow_run_id=workflow_run_id,
            )
            if state != "RETRY_RESERVED":
                raise SafeBaselineRegistrationError(
                    "build manifest can be bound only while RESERVED"
                )
            validate_resume_coordinates(
                activation,
                source_sha=source_sha,
                workflow_run_id=workflow_run_id,
                workflow_attempt=workflow_attempt,
            )
            activation_attempt = int(activation.get("workflow_attempt") or 1)
            if build_artifact_attempt != activation_attempt:
                raise SafeBaselineRegistrationError(
                    "build artifact attempt does not match the RESERVED fence"
                )
            existing = str(activation.get("manifest_sha256") or "").strip().lower()
            if existing:
                if existing != manifest_sha256:
                    raise SafeBaselineRegistrationError(
                        "RESERVED build manifest is immutable once assigned"
                    )
                if (
                    str(activation.get("build_artifact_id") or "") != clean_artifact_id
                    or str(activation.get("build_artifact_digest") or "").lower()
                    != clean_artifact_digest
                ):
                    raise SafeBaselineRegistrationError(
                        "RESERVED build artifact coordinates do not match the bound manifest"
                    )
                return {
                    "action": "bind-build",
                    "state": "BUILD_ALREADY_BOUND",
                    "activation_id": activation["id"],
                    "version": activation["version"],
                    "workflow_attempt": activation_attempt,
                    "manifest_sha256": existing,
                    "build_artifact_id": clean_artifact_id,
                    "build_artifact_digest": clean_artifact_digest,
                }
            result = connection.execute(
                text(
                    """
                    UPDATE release_activations
                    SET manifest_sha256 = :manifest_sha256,
                        build_artifact_id = :build_artifact_id,
                        build_artifact_digest = :build_artifact_digest,
                        version = version + 1
                    WHERE id = CAST(:activation_id AS uuid)
                      AND version = :expected_version
                      AND phase = 'RESERVED'
                      AND manifest_sha256 IS NULL
                      AND build_artifact_id IS NULL
                      AND build_artifact_digest IS NULL
                      AND source_sha = :source_sha
                      AND workflow_run_id = :workflow_run_id
                    RETURNING id::text, version
                    """
                ),
                {
                    "manifest_sha256": manifest_sha256,
                    "build_artifact_id": clean_artifact_id,
                    "build_artifact_digest": clean_artifact_digest,
                    "activation_id": activation["id"],
                    "expected_version": activation["version"],
                    "source_sha": source_sha,
                    "workflow_run_id": workflow_run_id,
                },
            ).mappings().one_or_none()
            if result is None:
                raise SafeBaselineRegistrationError("build manifest CAS lost")
        return {
            "action": "bind-build",
            "state": "BUILD_BOUND",
            "activation_id": result["id"],
            "version": result["version"],
            "workflow_attempt": build_artifact_attempt,
            "manifest_sha256": manifest_sha256,
            "build_artifact_id": clean_artifact_id,
            "build_artifact_digest": clean_artifact_digest,
        }
    finally:
        engine.dispose()


def _recover_deployment(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    runtime_bundle_id: str,
    manifest_sha256: str,
    token: str,
    project_id: str,
    team_id: str,
) -> dict[str, Any]:
    manifest_sha256 = _validate_sha(
        manifest_sha256,
        name="build manifest SHA-256",
    )
    preflight = _preflight(
        database_url,
        source_sha=source_sha,
        workflow_run_id=workflow_run_id,
        workflow_attempt=workflow_attempt,
    )
    if preflight["state"] != "RETRY_RESERVED":
        raise SafeBaselineRegistrationError("deployment recovery is allowed only from RESERVED")
    base_params = {"projectId": project_id, "limit": "100", "target": "production"}
    if team_id:
        base_params["teamId"] = team_id
    exact_matches_by_id: dict[str, dict[str, str]] = {}
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _page_number in range(1, MAX_DEPLOYMENT_PAGES + 1):
        params = dict(base_params)
        if cursor is not None:
            params["until"] = cursor
        response = httpx.get(
            "https://api.vercel.com/v6/deployments",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("deployments"), list):
            raise SafeBaselineRegistrationError("Vercel deployment recovery returned an invalid page")
        for item in payload["deployments"]:
            if not isinstance(item, dict):
                continue
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            if (
                meta.get("vowpicSourceSha") == source_sha
                and meta.get("vowpicRuntimeBundleId") == runtime_bundle_id
                and meta.get("vowpicBuildSha256") == manifest_sha256
                and meta.get("vowpicReleaseRole") == "SAFE_BASELINE"
            ):
                deployment_id = str(item.get("uid") or item.get("id") or "").strip()
                deployment_url = _vercel_deployment_url(item.get("url"))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", deployment_id) or not deployment_url:
                    raise SafeBaselineRegistrationError(
                        "Vercel returned an exact deployment with invalid coordinates"
                    )
                reported_states = {
                    str(value).strip().upper()
                    for value in (item.get("state"), item.get("readyState"))
                    if str(value or "").strip()
                }
                if len(reported_states) > 1:
                    raise SafeBaselineRegistrationError(
                        "Vercel returned conflicting states for one exact deployment"
                    )
                exact_match = {
                    "deployment_id": deployment_id,
                    "deployment_url": deployment_url,
                    "manifest_sha256": manifest_sha256,
                    "state": next(iter(reported_states), "UNKNOWN"),
                }
                previous = exact_matches_by_id.setdefault(deployment_id, exact_match)
                if previous != exact_match:
                    raise SafeBaselineRegistrationError(
                        "Vercel returned conflicting coordinates for one deployment"
                    )
        pagination = payload.get("pagination")
        next_value = pagination.get("next") if isinstance(pagination, dict) else None
        if next_value is None or next_value == "":
            break
        next_cursor = str(next_value).strip()
        if (
            not re.fullmatch(r"[0-9]{1,20}", next_cursor)
            or next_cursor in seen_cursors
        ):
            raise SafeBaselineRegistrationError("Vercel deployment pagination cursor is invalid or repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise SafeBaselineRegistrationError(
            f"Vercel deployment recovery exceeded {MAX_DEPLOYMENT_PAGES} pages"
        )
    exact_matches = list(exact_matches_by_id.values())
    if len(exact_matches) > 1:
        raise SafeBaselineRegistrationError("ambiguous exact deployment recovery candidates")
    if exact_matches and exact_matches[0]["state"] != "READY":
        raise SafeBaselineRegistrationError(
            "the exact staged deployment is not READY; refusing a duplicate deploy"
        )
    candidates = [
        {key: value for key, value in match.items() if key != "state"}
        for match in exact_matches
    ]
    decision = recovery_decision("RESERVED", candidate_count=len(candidates))
    return {
        "action": "recover-deployment",
        "decision": decision,
        "candidate_count": len(candidates),
        "candidate": candidates[0] if candidates else None,
        "manifest_sha256": manifest_sha256,
        "runtime_bundle_id": runtime_bundle_id,
    }


def _recover_promotion(
    database_url: str,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    token: str,
    project_id: str,
    team_id: str,
    formal_domain: str,
) -> dict[str, Any]:
    preflight = _preflight(
        database_url,
        source_sha=source_sha,
        workflow_run_id=workflow_run_id,
        workflow_attempt=workflow_attempt,
    )
    state = str(preflight["state"])
    if state not in {
        "RETRY_PROMOTION_ARMED",
        "RETRY_PROMOTED",
        "RETRY_FORMAL_VERIFIED",
    }:
        raise SafeBaselineRegistrationError(
            "promotion recovery requires an armed, PROMOTED, or FORMAL_VERIFIED activation"
        )
    activation = preflight.get("activation") or {}
    target_id = str(activation.get("api_deployment_id") or "").strip()
    target_url = str(activation.get("api_deployment_url") or "").strip()
    if not target_id or not target_url:
        raise SafeBaselineRegistrationError("recorded STAGED deployment coordinates are incomplete")
    parsed = urlsplit(
        formal_domain.strip()
        if "://" in formal_domain
        else f"https://{formal_domain.strip()}"
    )
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError("formal domain must be one HTTPS hostname without a path")
    params = {"teamId": team_id} if team_id else {}
    response = httpx.get(
        f"https://api.vercel.com/v13/deployments/{quote(parsed.hostname, safe='')}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if response.status_code == 404:
        raise SafeBaselineRegistrationError(
            "formal domain lookup cannot prove whether Promote already completed"
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SafeBaselineRegistrationError(
            "Vercel formal-domain recovery returned invalid JSON"
        )
    response_project_id = str(
        payload.get("projectId")
        or ((payload.get("project") or {}).get("id") if isinstance(payload.get("project"), dict) else "")
        or ""
    ).strip()
    if response_project_id != project_id:
        raise SafeBaselineRegistrationError("formal domain resolved outside the protected Vercel project")
    ready_state = str(payload.get("readyState") or payload.get("state") or "").upper()
    if ready_state != "READY":
        raise SafeBaselineRegistrationError("formal domain deployment is not READY")
    formal_id = str(payload.get("uid") or payload.get("id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", formal_id):
        raise SafeBaselineRegistrationError("formal domain deployment ID is missing or invalid")
    project_response = httpx.get(
        f"https://api.vercel.com/v9/projects/{quote(project_id, safe='')}",
        params={"teamId": team_id, "rollbackInfo": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    project_response.raise_for_status()
    project_payload = project_response.json()
    if not isinstance(project_payload, dict):
        raise SafeBaselineRegistrationError("Vercel project recovery returned invalid JSON")
    if (
        str(project_payload.get("id") or "").strip() != project_id
        or str(project_payload.get("accountId") or "").strip() != team_id
    ):
        raise SafeBaselineRegistrationError(
            "promotion status resolved outside the protected Vercel project and org"
        )
    rolling_release = project_payload.get("rollingRelease")
    if rolling_release is not None and rolling_release is not False:
        raise SafeBaselineRegistrationError(
            "safe-baseline promotion forbids a Vercel rolling release"
        )

    last_request = project_payload.get("lastAliasRequest")
    if last_request == {}:
        last_request = None
    if last_request is not None and not isinstance(last_request, dict):
        raise SafeBaselineRegistrationError("Vercel last alias request is malformed")
    target_promotion_succeeded = False
    if isinstance(last_request, dict):
        request_type = str(last_request.get("type") or "").strip()
        request_status = str(last_request.get("jobStatus") or "").strip()
        request_target = str(last_request.get("toDeploymentId") or "").strip()
        requested_at = last_request.get("requestedAt")
        if (
            request_type not in {"promote", "rollback"}
            or request_status
            not in {"pending", "in-progress", "failed", "skipped", "succeeded"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_target)
            or isinstance(requested_at, bool)
            or not isinstance(requested_at, (int, float))
            or requested_at <= 0
        ):
            raise SafeBaselineRegistrationError("Vercel last alias request is incomplete")
        if request_target == target_id:
            if request_type != "promote" or request_status != "succeeded" or formal_id != target_id:
                raise SafeBaselineRegistrationError(
                    "the staged deployment already has a promotion request; refusing a second Promote"
                )
            target_promotion_succeeded = True
        elif request_status in {"pending", "in-progress"}:
            raise SafeBaselineRegistrationError(
                "another Vercel alias request is still active"
            )
    if formal_id == target_id and not target_promotion_succeeded:
        raise SafeBaselineRegistrationError(
            "formal-domain target is visible but Vercel promotion success proof is missing"
        )
    phase = state.removeprefix("RETRY_")
    decision = promotion_recovery_decision(
        phase,
        formal_deployment_id=formal_id,
        target_deployment_id=target_id,
    )
    return {
        "action": "recover-promotion",
        "decision": decision,
        "activation_phase": phase,
        "target_deployment_id": target_id,
        "target_deployment_url": target_url,
        "formal_deployment_id": formal_id,
        "formal_domain": parsed.hostname,
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=(
            "preflight",
            "verify-runtime-secret",
            "materialize-deploy-root",
            "adopt-reserved",
            "adopt-staged-verifier",
            "rearm-reserved-build",
            "rearm-staged",
            "bind-build",
            "recover-deployment",
            "recover-promotion",
        ),
    )
    parser.add_argument("--phase", choices=PHASE_SEQUENCE)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument(
        "--inventory-database-url-env",
        default="PRODUCTION_READ_ONLY_DATABASE_URL",
    )
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--runner-sha")
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--workflow-attempt", type=int, default=int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-workflow-run-id")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--approval-id-env", default="SAFE_BASELINE_APPROVAL_ID")
    parser.add_argument("--inventory-report")
    parser.add_argument("--restore-report")
    parser.add_argument("--evidence-prefix", default="artifacts/security-baseline")
    parser.add_argument("--runtime-bundle-id")
    parser.add_argument("--deployment-id")
    parser.add_argument("--deployment-url")
    parser.add_argument("--build-output")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--build-artifact-attempt", type=int)
    parser.add_argument("--build-artifact-id")
    parser.add_argument("--build-artifact-digest")
    parser.add_argument("--source-output")
    parser.add_argument("--source-root")
    parser.add_argument("--destination-root")
    parser.add_argument("--formal-report")
    parser.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
    parser.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
    parser.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
    parser.add_argument("--formal-domain")
    parser.add_argument("--vercel-env-file")
    parser.add_argument(
        "--runtime-secret-env",
        default="ACCEPTANCE_IDENTITY_HMAC_KEY",
    )
    parser.add_argument(
        "--runtime-secret-fingerprint",
        default="ACCEPTANCE_IDENTITY_HMAC_KEY_SHA256",
    )
    parser.add_argument("--runtime-secret-evidence")
    parser.add_argument(
        "--inject-failure",
        choices=("BEFORE_MIGRATION", "AFTER_MIGRATION_BEFORE_RESERVATION", "AFTER_RESERVATION_BEFORE_COMMIT"),
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        args.source_sha = _validate_sha(args.source_sha, name="source SHA", lengths=(40, 64))
        args.runner_sha = _validate_sha(
            args.runner_sha or args.source_sha,
            name="runner SHA",
            lengths=(40, 64),
        )
        if not args.workflow_run_id.strip() or args.workflow_attempt < 1:
            raise ValueError("workflow run ID and positive attempt are required")
        if bool(args.action) == bool(args.phase):
            raise ValueError("choose exactly one of --action or --phase")
        if args.action == "verify-runtime-secret":
            expected_secret = _required_env(args.runtime_secret_env)
            vercel_token = _required_env(args.vercel_token_env)
            project_id = _required_env(args.vercel_project_id_env)
            team_id = _required_env(args.vercel_team_id_env)
            if not all(
                (
                    expected_secret,
                    args.vercel_env_file,
                    vercel_token,
                    project_id,
                    team_id,
                )
            ):
                print(
                    "NOT_RUN: protected runtime secret and Vercel proof coordinates are required",
                    file=sys.stderr,
                )
                return NOT_RUN_EXIT
            result = verify_sensitive_runtime_secret_proof(
                Path(args.vercel_env_file),
                secret_name=args.runtime_secret_env,
                fingerprint_name=args.runtime_secret_fingerprint,
                expected_secret=expected_secret,
                vercel_token=vercel_token,
                project_id=project_id,
                team_id=team_id,
                source_sha=args.source_sha,
                runner_sha=args.runner_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
            )
            if args.output:
                _write_create_once(Path(args.output), result)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.action == "materialize-deploy-root":
            if not all((args.source_output, args.source_root, args.destination_root)):
                raise ValueError(
                    "source output, source root, and destination root are required"
                )
            project_id = _required_env(args.vercel_project_id_env)
            org_id = _required_env(args.vercel_team_id_env)
            if not project_id or not org_id:
                print(
                    "NOT_RUN: protected Vercel project coordinates are required",
                    file=sys.stderr,
                )
                return NOT_RUN_EXIT
            result = materialize_vercel_deploy_root(
                Path(args.source_output),
                source_root=Path(args.source_root),
                destination_root=Path(args.destination_root),
                source_sha=args.source_sha,
                runner_sha=args.runner_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                expected_project_id=project_id,
                expected_org_id=org_id,
            )
            if args.output:
                _write_create_once(Path(args.output), result)
            print(json.dumps(result, sort_keys=True))
            return 0
        database_url = _required_env(args.database_url_env)
        if not database_url:
            print(f"NOT_RUN: protected database variable {args.database_url_env} is missing", file=sys.stderr)
            return NOT_RUN_EXIT
        if args.action == "preflight":
            result = _preflight(
                database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                runner_sha=args.runner_sha,
            )
        elif args.action == "adopt-reserved":
            approval = _required_env(args.approval_id_env)
            if not approval:
                print("NOT_RUN: protected reservation approval is required", file=sys.stderr)
                return NOT_RUN_EXIT
            result = _adopt_reserved(
                database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                expected_source_sha=args.expected_source_sha or "",
                expected_workflow_run_id=args.expected_workflow_run_id or "",
                expected_version=args.expected_version or 0,
                approval=approval,
                evidence_prefix=args.evidence_prefix,
            )
        elif args.action == "rearm-reserved-build":
            approval = _required_env(args.approval_id_env)
            if not approval:
                print("NOT_RUN: protected reserved-build approval is required", file=sys.stderr)
                return NOT_RUN_EXIT
            result = _rearm_invalid_reserved_build(
                database_url,
                source_sha=args.source_sha,
                runner_sha=args.runner_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                expected_source_sha=args.expected_source_sha or "",
                expected_workflow_run_id=args.expected_workflow_run_id or "",
                expected_version=args.expected_version or 0,
                expected_build_artifact_id=args.build_artifact_id or "",
                expected_build_artifact_digest=args.build_artifact_digest or "",
                approval=approval,
                evidence_prefix=args.evidence_prefix,
            )
        elif args.action == "adopt-staged-verifier":
            approval = _required_env(args.approval_id_env)
            if not approval:
                print("NOT_RUN: protected staged-verifier approval is required", file=sys.stderr)
                return NOT_RUN_EXIT
            result = _adopt_staged_verifier(
                database_url,
                source_sha=args.source_sha,
                runner_sha=args.runner_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                expected_source_sha=args.expected_source_sha or "",
                expected_workflow_run_id=args.expected_workflow_run_id or "",
                expected_version=args.expected_version or 0,
                approval=approval,
                evidence_prefix=args.evidence_prefix,
            )
        elif args.action == "rearm-staged":
            approval = _required_env(args.approval_id_env)
            project_id = _required_env(args.vercel_project_id_env)
            team_id = _required_env(args.vercel_team_id_env)
            if not all((approval, args.runtime_secret_evidence, project_id, team_id)):
                print(
                    "NOT_RUN: protected staged-rearm approval and Vercel proof are required",
                    file=sys.stderr,
                )
                return NOT_RUN_EXIT
            result = _rearm_invalid_staged(
                database_url,
                source_sha=args.source_sha,
                runner_sha=args.runner_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                expected_source_sha=args.expected_source_sha or "",
                expected_workflow_run_id=args.expected_workflow_run_id or "",
                expected_version=args.expected_version or 0,
                approval=approval,
                evidence_prefix=args.evidence_prefix,
                runtime_secret_evidence=Path(args.runtime_secret_evidence),
                project_id=project_id,
                team_id=team_id,
            )
        elif args.action == "bind-build":
            result = _bind_build_manifest(
                database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                build_artifact_attempt=args.build_artifact_attempt or 0,
                build_artifact_id=args.build_artifact_id or "",
                build_artifact_digest=args.build_artifact_digest or "",
                manifest_sha256=args.manifest_sha256 or "",
            )
        elif args.action == "recover-deployment":
            token = _required_env(args.vercel_token_env)
            project_id = _required_env(args.vercel_project_id_env)
            team_id = _required_env(args.vercel_team_id_env)
            runtime_bundle_id = str(args.runtime_bundle_id or "").strip()
            manifest_sha256 = _validate_sha(
                args.manifest_sha256 or "",
                name="build manifest SHA-256",
            )
            if not token or not project_id or not team_id or not re.fullmatch(r"rtb_[0-9a-f]{64}", runtime_bundle_id):
                print("NOT_RUN: Vercel recovery coordinates are incomplete", file=sys.stderr)
                return NOT_RUN_EXIT
            result = _recover_deployment(
                database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                runtime_bundle_id=runtime_bundle_id,
                manifest_sha256=manifest_sha256,
                token=token,
                project_id=project_id,
                team_id=team_id,
            )
        elif args.action == "recover-promotion":
            token = _required_env(args.vercel_token_env)
            project_id = _required_env(args.vercel_project_id_env)
            team_id = _required_env(args.vercel_team_id_env)
            if not token or not project_id or not team_id or not args.formal_domain:
                print(
                    "NOT_RUN: protected Vercel coordinates and formal domain are required",
                    file=sys.stderr,
                )
                return NOT_RUN_EXIT
            result = _recover_promotion(
                database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                token=token,
                project_id=project_id,
                team_id=team_id,
                formal_domain=args.formal_domain,
            )
        elif args.phase == "RESERVED":
            approval = _required_env(args.approval_id_env)
            inventory_database_url = _required_env(args.inventory_database_url_env)
            if not approval or not inventory_database_url or not args.inventory_report or not args.restore_report:
                print(
                    "NOT_RUN: approval, read-only database, and inventory/restore evidence are required",
                    file=sys.stderr,
                )
                return NOT_RUN_EXIT
            result = _reserve(
                database_url,
                inventory_database_url=inventory_database_url,
                source_sha=args.source_sha,
                workflow_run_id=args.workflow_run_id,
                workflow_attempt=args.workflow_attempt,
                approval=approval,
                inventory_report=Path(args.inventory_report),
                restore_report=Path(args.restore_report),
                evidence_prefix=args.evidence_prefix,
                inject_failure=args.inject_failure,
            )
        else:
            result = _advance_phase(database_url, args)
        if args.output:
            _write_create_once(Path(args.output), result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, SafeBaselineRegistrationError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
