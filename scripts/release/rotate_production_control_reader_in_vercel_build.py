"""Rotate and prove one fixed control-reader login in an unaliased Vercel build.

Vercel supplies the existing Production ``DATABASE_URL`` to the remote build
without disclosing it to GitHub. The caller supplies the three protected
application credentials as build variables. This module performs the database
mutation and proof only; it never emits or persists a credential value.
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
        if proof.get("passed") is not True:
            raise ValueError("Production credential proof did not pass")
        credentials = {
            kind: dict(facts)
            for kind, facts in dict(proof["credentials"]).items()
        }
        if set(credentials) != {"runtime", "control_writer", "control_reader"}:
            raise ValueError("Production credential proof set is invalid")
        database = str(proof["database"])
        if not database:
            raise ValueError("Production credential proof database is missing")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Production credential proof shape is invalid") from exc
    return {
        "schema": SCHEMA,
        "state": "PASSED",
        "credential_rotation": "unaliased_vercel_production_build",
        "database": database,
        "credentials": credentials,
    }


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return "private build output could not be written"
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
        write_build_output()
    except (OSError, ValueError, psycopg2.Error) as exc:
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
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
