"""Rotate one fixed control-reader login inside an unaliased Vercel build.

This module is intentionally build-only.  Vercel supplies the existing
Production ``DATABASE_URL`` to the remote build without disclosing it to the
calling GitHub job.  The caller supplies the three protected application
credentials as build variables, and the shared repair contract verifies the
database target and least-privilege role before changing the password.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import sys
from typing import Mapping
from urllib.parse import unquote, urlsplit

import psycopg2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from repair_production_control_reader_credential import (
    encrypt_secret,
    prove_control_reader_after_pooler_propagation,
    recover_control_reader_url,
    rotate_control_reader_password,
)


SCHEMA = "vowpic.vercel-build-control-reader-repair.v1"
CREDENTIAL_ENVELOPE_SCHEMA = "vowpic.encrypted-control-reader-credential.v1"
DELIVERY_SCHEMA = "vowpic.control-reader-repair-delivery.v1"
ADMIN_DATABASE_URL_ENV = "DATABASE_URL"
RUNTIME_DATABASE_URL_ENV = "PRODUCTION_RUNTIME_DATABASE_URL"
CONTROL_WRITER_DATABASE_URL_ENV = "PRODUCTION_CONTROL_PLANE_DATABASE_URL"
CONTROL_READER_SECRET_ENV = "PRODUCTION_CONTROL_READ_DATABASE_URL"
RECIPIENT_PUBLIC_KEY_B64_ENV = "CONTROL_READER_RECIPIENT_PUBLIC_KEY_B64"
BUILD_OUTPUT_DIRECTORY = Path(".vowpic-control-reader-repair-output")


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise ValueError(f"required protected build variable is missing: {name}")
    return value


def recipient_public_key(environment: Mapping[str, str]) -> bytes:
    encoded = _required_environment(environment, RECIPIENT_PUBLIC_KEY_B64_ENV)
    try:
        public_key_pem = base64.b64decode(encoded, validate=True)
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("recipient public key is invalid") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("recipient public key must be RSA with at least 3072 bits")
    return public_key_pem


def rotate_and_prove(
    environment: Mapping[str, str],
    public_key_pem: bytes,
) -> tuple[bytes, dict[str, object]]:
    admin_url = _required_environment(environment, ADMIN_DATABASE_URL_ENV)
    runtime_url = _required_environment(environment, RUNTIME_DATABASE_URL_ENV)
    writer_url = _required_environment(
        environment,
        CONTROL_WRITER_DATABASE_URL_ENV,
    )
    reader_secret = _required_environment(environment, CONTROL_READER_SECRET_ENV)
    reader_url = recover_control_reader_url(runtime_url, writer_url, reader_secret)
    encrypted_reader_url = encrypt_secret(reader_url, public_key_pem)
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
    return encrypted_reader_url, {
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


def write_build_output(
    encrypted_reader_url: bytes,
    proof: Mapping[str, object],
) -> None:
    if not encrypted_reader_url:
        raise ValueError("encrypted reader URL is empty")
    BUILD_OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    credential = {
        "algorithm": "RSA-OAEP-SHA256",
        "ciphertext_b64": base64.b64encode(encrypted_reader_url).decode(
            "ascii"
        ),
        "schema": CREDENTIAL_ENVELOPE_SCHEMA,
    }
    BUILD_OUTPUT_DIRECTORY.joinpath("index.html").write_text(
        json.dumps(
            {
                "credential": credential,
                "proof": proof,
                "schema": DELIVERY_SCHEMA,
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        public_key_pem = recipient_public_key(os.environ)
        encrypted_reader_url, result = rotate_and_prove(os.environ, public_key_pem)
        write_build_output(encrypted_reader_url, result)
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
