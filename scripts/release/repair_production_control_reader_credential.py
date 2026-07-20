"""Rotate and prove the protected Production control-reader credential.

The generated connection URL is encrypted in-process to a one-time RSA public
key. Plaintext credentials are never written to disk or emitted to stdout.
"""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

import psycopg2
from psycopg2.extras import RealDictCursor
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from verify_production_database_credentials import (
    EXPECTED_CURRENT_USERS,
    EXPECTED_SESSIONS,
    _sync_url,
    prove_production_database_credentials,
)


CONTROL_READER_KIND = "control_reader"
CONTROL_READER_LOGIN = EXPECTED_SESSIONS[CONTROL_READER_KIND]
CONTROL_READER_ROLE = EXPECTED_CURRENT_USERS[CONTROL_READER_KIND]


def _source_coordinates(url: str, expected_login: str) -> dict[str, Any]:
    parsed = urlsplit(_sync_url(url))
    username = unquote(parsed.username or "")
    database = parsed.path.lstrip("/")
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "postgresql"
        or not username
        or not parsed.password
        or not host
        or not database
        or parsed.port not in {5432, 6543}
        or parse_qs(parsed.query).get("sslmode") != ["require"]
    ):
        raise ValueError("source Production database URL is incomplete")
    login, separator, project_ref = username.partition(".")
    if login != expected_login or not separator or not project_ref:
        raise ValueError("source Production database URL has an invalid login")
    if not host.endswith(".pooler.supabase.com"):
        raise ValueError("source Production database URL is not a Supabase pooler")
    return {
        "host": host,
        "port": int(parsed.port),
        "database": database,
        "project_ref": project_ref,
    }


def build_control_reader_url(
    runtime_url: str,
    control_writer_url: str,
    password: str,
) -> str:
    if not password or any(character.isspace() for character in password):
        raise ValueError("generated password is invalid")
    runtime = _source_coordinates(runtime_url, EXPECTED_SESSIONS["runtime"])
    writer = _source_coordinates(
        control_writer_url,
        EXPECTED_SESSIONS["control_writer"],
    )
    if runtime != writer:
        raise ValueError("source Production database URLs do not share one target")
    username = f"{CONTROL_READER_LOGIN}.{runtime['project_ref']}"
    netloc = (
        f"{quote(username, safe='')}:{quote(password, safe='')}@"
        f"{runtime['host']}:{runtime['port']}"
    )
    return urlunsplit(
        (
            "postgresql",
            netloc,
            f"/{runtime['database']}",
            "sslmode=require",
            "",
        )
    )


def validate_control_reader_role(facts: dict[str, Any]) -> None:
    if (
        facts.get("rolcanlogin") is not True
        or facts.get("rolinherit") is not True
        or facts.get("rolsuper") is not False
        or facts.get("rolcreatedb") is not False
        or facts.get("rolcreaterole") is not False
        or facts.get("rolreplication") is not False
        or facts.get("rolbypassrls") is not False
        or set(facts.get("direct_memberships") or ()) != {CONTROL_READER_ROLE}
    ):
        raise ValueError("Production control-reader login is not least privilege")
    settings = set(facts.get("role_settings") or ())
    if (
        f"role={CONTROL_READER_ROLE}" not in settings
        or "default_transaction_read_only=on" not in settings
    ):
        raise ValueError("Production control-reader login defaults are unsafe")


def rotate_control_reader_password(admin_url: str, password: str) -> None:
    with psycopg2.connect(
        _sync_url(admin_url),
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        application_name="vowpic-production-control-reader-credential-repair",
    ) as connection:
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT role.rolcanlogin,
                       role.rolinherit,
                       role.rolsuper,
                       role.rolcreatedb,
                       role.rolcreaterole,
                       role.rolreplication,
                       role.rolbypassrls,
                       COALESCE(role.rolconfig, ARRAY[]::text[]) AS role_settings,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = role.oid
                       ), ARRAY[]::name[]) AS direct_memberships
                FROM pg_roles role
                WHERE role.rolname = %s
                """,
                (CONTROL_READER_LOGIN,),
            )
            facts = cursor.fetchone()
            if facts is None:
                raise ValueError("Production control-reader login is missing")
            validate_control_reader_role(dict(facts))
            cursor.execute(
                f'ALTER ROLE "{CONTROL_READER_LOGIN}" PASSWORD %s',
                (password,),
            )
        connection.commit()


def encrypt_secret(secret: str, public_key_pem: bytes) -> bytes:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("recipient public key must be RSA with at least 3072 bits")
    return public_key.encrypt(
        secret.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-database-url-env", required=True)
    parser.add_argument("--runtime-database-url-env", required=True)
    parser.add_argument("--control-writer-database-url-env", required=True)
    parser.add_argument("--recipient-public-key", type=Path, required=True)
    parser.add_argument("--encrypted-url-output", type=Path, required=True)
    parser.add_argument("--proof-output", type=Path, required=True)
    return parser.parse_args()


def _required_environment(name: str) -> str:
    import os

    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value


def main() -> int:
    args = _parse_args()
    admin_url = _required_environment(args.admin_database_url_env)
    runtime_url = _required_environment(args.runtime_database_url_env)
    writer_url = _required_environment(args.control_writer_database_url_env)
    password = secrets.token_hex(32)
    reader_url = build_control_reader_url(runtime_url, writer_url, password)

    rotate_control_reader_password(admin_url, password)
    proof = prove_production_database_credentials(
        {
            "runtime": runtime_url,
            "control_writer": writer_url,
            "control_reader": reader_url,
        }
    )
    encrypted_url = encrypt_secret(
        reader_url,
        args.recipient_public_key.read_bytes(),
    )

    args.encrypted_url_output.parent.mkdir(parents=True, exist_ok=True)
    args.encrypted_url_output.write_bytes(encrypted_url)
    args.proof_output.parent.mkdir(parents=True, exist_ok=True)
    args.proof_output.write_text(
        json.dumps(proof, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
