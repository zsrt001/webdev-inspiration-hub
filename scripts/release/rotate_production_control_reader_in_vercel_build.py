"""Rotate one fixed control-reader login inside an unaliased Vercel build.

This module is intentionally build-only.  Vercel supplies the existing
Production ``DATABASE_URL`` to the remote build without disclosing it to the
calling GitHub job.  The caller supplies the three protected application
credentials as build variables, and the shared repair contract verifies the
database target and least-privilege role before changing the password.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import unquote, urlsplit

import psycopg2

from repair_production_control_reader_credential import (
    prove_control_reader_after_pooler_propagation,
    recover_control_reader_url,
    rotate_control_reader_password,
)


SCHEMA = "vowpic.vercel-build-control-reader-repair.v1"
ADMIN_DATABASE_URL_ENV = "DATABASE_URL"
RUNTIME_DATABASE_URL_ENV = "PRODUCTION_RUNTIME_DATABASE_URL"
CONTROL_WRITER_DATABASE_URL_ENV = "PRODUCTION_CONTROL_PLANE_DATABASE_URL"
CONTROL_READER_SECRET_ENV = "PRODUCTION_CONTROL_READ_DATABASE_URL"
BUILD_OUTPUT_DIRECTORY = Path(".vowpic-control-reader-repair-output")


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise ValueError(f"required protected build variable is missing: {name}")
    return value


def rotate_and_prove(environment: Mapping[str, str]) -> dict[str, object]:
    admin_url = _required_environment(environment, ADMIN_DATABASE_URL_ENV)
    runtime_url = _required_environment(environment, RUNTIME_DATABASE_URL_ENV)
    writer_url = _required_environment(
        environment,
        CONTROL_WRITER_DATABASE_URL_ENV,
    )
    reader_secret = _required_environment(environment, CONTROL_READER_SECRET_ENV)
    reader_url = recover_control_reader_url(runtime_url, writer_url, reader_secret)
    password = unquote(urlsplit(reader_url).password or "")
    rotate_control_reader_password(
        admin_url,
        runtime_url,
        writer_url,
        password,
    )
    proof = prove_control_reader_after_pooler_propagation(
        runtime_url,
        writer_url,
        reader_url,
    )
    try:
        reader_facts = dict(proof["credentials"]["control_reader"])
        database = str(proof["database"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Production credential proof shape is invalid") from exc
    return {
        "schema": SCHEMA,
        "state": "PASSED",
        "credential_rotation": "unaliased_vercel_production_build",
        "database": database,
        "control_reader": reader_facts,
    }


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    return "database operation failed"


def write_build_output() -> None:
    BUILD_OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    BUILD_OUTPUT_DIRECTORY.joinpath("index.html").write_text(
        "<!doctype html><title>Private repair completed</title>\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        result = rotate_and_prove(os.environ)
    except (ValueError, psycopg2.Error) as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "state": "FAILED",
                    "reason": _safe_failure_reason(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    write_build_output()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
