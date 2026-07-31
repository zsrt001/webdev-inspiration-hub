#!/usr/bin/env python3
"""Rotate Preview base logins and seal their URLs to one ephemeral public key."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.parse import unquote, urlsplit

import asyncpg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import psycopg2


RELEASE_DIR = Path(__file__).resolve().parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from production_database_login_proof import RUNTIME_LOGIN, WRITER_LOGIN  # noqa: E402
from provision_production_database_logins import (  # noqa: E402
    provision_database_logins,
)


ENVELOPE_SCHEMA = "vowpic.preview-database-credentials-envelope.v1"
PROOF_SCHEMA = "vowpic.preview-database-login-repair-proof.v1"
PLAINTEXT_SCHEMA = "vowpic.preview-database-credentials.v1"


def load_delivery_public_key(value: str) -> tuple[rsa.RSAPublicKey, str]:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
        key = serialization.load_der_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("delivery public key is invalid") from exc
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError("delivery public key must be RSA-3072 or stronger")
    return key, hashlib.sha256(raw).hexdigest()


def seal_credentials(
    *,
    public_key: rsa.RSAPublicKey,
    public_key_sha256: str,
    source_sha: str,
    runtime_url: str,
    writer_url: str,
) -> dict[str, str]:
    header = {
        "schema": ENVELOPE_SCHEMA,
        "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
        "source_sha": source_sha,
        "public_key_sha256": public_key_sha256,
    }
    associated_data = json.dumps(
        header,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    plaintext = json.dumps(
        {
            "schema": PLAINTEXT_SCHEMA,
            "source_sha": source_sha,
            "runtime_url": runtime_url,
            "writer_url": writer_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    encrypted_key = public_key.encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return {
        **header,
        "associated_data_b64": base64.b64encode(associated_data).decode("ascii"),
        "encrypted_key_b64": base64.b64encode(encrypted_key).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
    }


def _pooler_project_ref(url: str, expected_login: str) -> str:
    parsed = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    username = unquote(parsed.username or "")
    login, separator, project_ref = username.partition(".")
    if (
        parsed.scheme != "postgresql"
        or login != expected_login
        or separator != "."
        or not re.fullmatch(r"[a-z0-9]{20}", project_ref)
        or not (parsed.hostname or "").endswith(".pooler.supabase.com")
        or parsed.port not in {5432, 6543}
    ):
        raise ValueError(f"{expected_login} Preview pooler URL is invalid")
    return project_ref


async def _asyncpg_login_facts(url: str, expected_login: str) -> dict[str, Any]:
    connection = await asyncpg.connect(
        url.replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=20,
        command_timeout=20,
        statement_cache_size=0,
    )
    try:
        row = await connection.fetchrow(
            "SELECT session_user, current_user, current_database() AS database"
        )
        if row is None:
            raise ValueError(f"{expected_login} asyncpg identity is missing")
        facts = dict(row)
        if (
            facts.get("session_user") != expected_login
            or facts.get("current_user") != expected_login
        ):
            raise ValueError(f"{expected_login} asyncpg identity is unexpected")
        if expected_login == RUNTIME_LOGIN:
            revisions = await connection.fetch(
                "SELECT version_num FROM public.alembic_version"
            )
            if not revisions:
                raise ValueError("runtime asyncpg schema revision is missing")
            facts["schema_revision_count"] = len(revisions)
        return facts
    finally:
        await connection.close()


async def prove_asyncpg_logins(runtime_url: str, writer_url: str) -> dict[str, Any]:
    runtime, writer = await asyncio.gather(
        _asyncpg_login_facts(runtime_url, RUNTIME_LOGIN),
        _asyncpg_login_facts(writer_url, WRITER_LOGIN),
    )
    if runtime["database"] != writer["database"]:
        raise ValueError("Preview base logins do not target one database")
    return {
        "database": runtime["database"],
        "runtime_session_user": runtime["session_user"],
        "runtime_current_user": runtime["current_user"],
        "runtime_schema_revision_count": runtime["schema_revision_count"],
        "writer_session_user": writer["session_user"],
        "writer_current_user": writer["current_user"],
    }


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PREVIEW_MIGRATION_DATABASE_URL")
    parser.add_argument("--public-key-env", default="DELIVERY_PUBLIC_KEY_B64")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--encrypted-output", type=Path, required=True)
    parser.add_argument("--proof-output", type=Path, required=True)
    args = parser.parse_args()
    migration_url = os.environ.get(args.database_url_env, "").strip()
    public_key_value = os.environ.get(args.public_key_env, "").strip()
    runtime_url = writer_url = ""
    try:
        if not migration_url:
            raise ValueError("Preview migration database URL is missing")
        if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
            raise ValueError("source SHA is invalid")
        public_key, public_key_sha256 = load_delivery_public_key(public_key_value)
        runtime_url, writer_url, database_proof = provision_database_logins(
            migration_url
        )
        runtime_ref = _pooler_project_ref(runtime_url, RUNTIME_LOGIN)
        writer_ref = _pooler_project_ref(writer_url, WRITER_LOGIN)
        if runtime_ref != writer_ref:
            raise ValueError("Preview base logins target different projects")
        envelope = seal_credentials(
            public_key=public_key,
            public_key_sha256=public_key_sha256,
            source_sha=args.source_sha,
            runtime_url=runtime_url,
            writer_url=writer_url,
        )
        _write_create_once(args.encrypted_output, envelope)
        asyncpg_proof = asyncio.run(prove_asyncpg_logins(runtime_url, writer_url))
        proof = {
            "schema": PROOF_SCHEMA,
            "state": "PROVISIONED",
            "source_sha": args.source_sha,
            "public_key_sha256": public_key_sha256,
            "preview_project_ref_sha256": hashlib.sha256(
                runtime_ref.encode("ascii")
            ).hexdigest(),
            "runtime_login": RUNTIME_LOGIN,
            "control_writer_login": WRITER_LOGIN,
            "database": str(database_proof["database"]),
            "credential_rotation": str(database_proof["credential_rotation"]),
            "asyncpg": asyncpg_proof,
        }
        _write_create_once(args.proof_output, proof)
        print(json.dumps({"schema": PROOF_SCHEMA, "state": "PROVISIONED"}))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        psycopg2.Error,
        asyncpg.PostgresError,
    ) as exc:
        detail = str(exc)
        for secret_value in (migration_url, runtime_url, writer_url):
            if secret_value:
                detail = detail.replace(secret_value, "[REDACTED]")
        detail = re.sub(r"postgres(?:ql)?://\S+", "[REDACTED]", detail)
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
