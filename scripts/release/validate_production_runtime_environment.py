"""Fail closed before a COMMERCIAL_7A deployment can consume runtime secrets."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
import re
import sys
from pathlib import Path


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_VALUES = {
    "RUNTIME_ENVIRONMENT": "production",
    "RELEASE_ROLE": "COMMERCIAL_7A",
    "TASK_EXECUTION_MODE": "backend",
    "STORAGE_PROVIDER": "vercel",
    "GENERATION_ENGINE": "evolink",
    "LLM_PROVIDER": "wenwen",
    "PAYMENT_PROVIDER": "creem",
    "RATE_LIMIT_ENABLED": "true",
    "QA_REQUIRE_VISION": "true",
    "GATEKEEPER_ALLOW_WITHOUT_PILLOW": "false",
    "QA_ALLOW_WITHOUT_PILLOW": "false",
    "ALLOW_MEMORY_FALLBACK": "false",
}


def _bootstrap_imports() -> None:
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


@contextmanager
def _temporary_environment(environ: dict[str, str]) -> Iterator[None]:
    original = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(environ)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _validate_expected_values(environ: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, expected in _EXPECTED_VALUES.items():
        actual = str(environ.get(name, "")).strip()
        if actual != expected:
            errors.append(f"{name} must be exactly {expected}")
    return errors


def _validate_positive_cost_cap(environ: dict[str, str]) -> list[str]:
    raw = str(environ.get("PRODUCTION_CANARY_MAX_COST_MINOR", "")).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ["PRODUCTION_CANARY_MAX_COST_MINOR must be a positive integer"]
    if value < 1:
        return ["PRODUCTION_CANARY_MAX_COST_MINOR must be a positive integer"]
    return []


def validate_environment(
    environ: dict[str, str],
    *,
    source_sha: str,
) -> list[str]:
    errors = _validate_expected_values(environ)
    errors.extend(_validate_positive_cost_cap(environ))
    if not _SHA40.fullmatch(str(source_sha or "").strip()):
        errors.append("SOURCE_SHA must be an exact 40-character lowercase commit")

    _bootstrap_imports()
    try:
        from app.core.config import Settings
        from app.core import runtime_checks
    except Exception:
        errors.append("Production runtime validator could not be imported")
        return sorted(set(errors))

    with _temporary_environment(environ):
        try:
            settings = Settings(
                _env_file=None,
                vercel_deployment_id="dpl_preflight",
                vercel_git_commit_sha=source_sha,
                runtime_bundle_id="rtb_" + ("0" * 64),
            )
        except Exception:
            errors.append("Production runtime configuration could not be parsed")
            return sorted(set(errors))

        original_settings = runtime_checks.settings
        try:
            runtime_checks.settings = settings
            errors.extend(runtime_checks.validate_commercial_config_values())
        finally:
            runtime_checks.settings = original_settings
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the immutable COMMERCIAL_7A runtime environment"
    )
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    errors = validate_environment(dict(os.environ), source_sha=args.source_sha)
    payload = {
        "schema": "vowpic.production-runtime-preflight.v1",
        "ok": not errors,
        "errors": errors,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
