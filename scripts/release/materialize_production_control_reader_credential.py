"""Normalize and RSA-encrypt the proven Production control-reader credential.

The workflow runs this only after the unaliased Vercel build has successfully
rotated and proved the same protected credential. Plaintext credentials remain
in process memory and are never written to disk or emitted to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping

from repair_production_control_reader_credential import (
    encrypt_secret,
    recover_control_reader_url,
)
from verify_production_database_credentials import validate_database_urls


SCHEMA = "vowpic.production-control-reader-credential-normalization.v1"
RUNTIME_DATABASE_URL_ENV = "PRODUCTION_RUNTIME_DATABASE_URL"
CONTROL_WRITER_DATABASE_URL_ENV = "PRODUCTION_CONTROL_PLANE_DATABASE_URL"
CONTROL_READER_SECRET_ENV = "PRODUCTION_CONTROL_READ_DATABASE_URL"


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise ValueError(f"required protected environment variable is missing: {name}")
    return value


def normalize_and_encrypt(
    environment: Mapping[str, str],
    public_key_pem: bytes,
) -> tuple[bytes, dict[str, object]]:
    runtime_url = _required_environment(environment, RUNTIME_DATABASE_URL_ENV)
    writer_url = _required_environment(
        environment,
        CONTROL_WRITER_DATABASE_URL_ENV,
    )
    reader_secret = _required_environment(environment, CONTROL_READER_SECRET_ENV)
    reader_url = recover_control_reader_url(runtime_url, writer_url, reader_secret)
    parsed = validate_database_urls(
        {
            "runtime": runtime_url,
            "control_writer": writer_url,
            "control_reader": reader_url,
        }
    )
    encrypted_url = encrypt_secret(reader_url, public_key_pem)
    return encrypted_url, {
        "schema": SCHEMA,
        "state": "NORMALIZED",
        "normalization": "deterministic_from_protected_production_secrets",
        "credential": parsed["control_reader"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipient-public-key", type=Path, required=True)
    parser.add_argument("--encrypted-url-output", type=Path, required=True)
    parser.add_argument("--normalization-output", type=Path, required=True)
    return parser.parse_args()


def _write_normalization(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _parse_args()
    try:
        encrypted_url, normalization = normalize_and_encrypt(
            os.environ,
            args.recipient_public_key.read_bytes(),
        )
        args.encrypted_url_output.parent.mkdir(parents=True, exist_ok=True)
        with args.encrypted_url_output.open("xb") as handle:
            handle.write(encrypted_url)
        _write_normalization(args.normalization_output, normalization)
    except (OSError, TypeError, ValueError):
        _write_normalization(
            args.normalization_output,
            {
                "schema": SCHEMA,
                "state": "FAILED",
                "reason": "protected credential normalization failed",
            },
        )
        print(
            "ERROR: protected Production control-reader credential normalization failed",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"schema": SCHEMA, "state": "NORMALIZED"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
