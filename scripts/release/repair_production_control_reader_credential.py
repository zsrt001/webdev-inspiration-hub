"""Recover and prove the protected Production control-reader credential.

The existing secret may be either the role password or a complete connection
URL. The normalized URL is encrypted in-process to a one-time RSA public key.
Plaintext credentials are never written to disk or emitted to stdout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from verify_production_database_credentials import (
    EXPECTED_CURRENT_USERS,
    EXPECTED_SESSIONS,
    _sync_url,
    prove_production_database_credentials,
    validate_database_urls,
)


CONTROL_READER_KIND = "control_reader"
CONTROL_READER_LOGIN = EXPECTED_SESSIONS[CONTROL_READER_KIND]
CONTROL_READER_ROLE = EXPECTED_CURRENT_USERS[CONTROL_READER_KIND]
POOLER_AUTH_RETRY_DELAYS_SECONDS = (0, 15, 30, 60)
VERCEL_ENV_ENDPOINT = "https://api.vercel.com/v10/projects/{project_id}/env"
FAILURE_SCHEMA = "vowpic.production-control-reader-credential-repair.v1"


def _source_coordinates(url: str, expected_login: str) -> dict[str, object]:
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


def recover_control_reader_url(
    runtime_url: str,
    control_writer_url: str,
    existing_secret: str,
) -> str:
    raw_value = existing_secret.strip()
    if not raw_value:
        raise ValueError("existing Production control-reader secret is invalid")
    value = "".join(raw_value.split())
    if not value:
        raise ValueError("existing Production control-reader secret is invalid")
    if value.startswith(("postgresql://", "postgresql+asyncpg://")):
        urls = {
            "runtime": runtime_url,
            "control_writer": control_writer_url,
            "control_reader": value,
        }
        validate_database_urls(urls)
        return _sync_url(value)
    if len(value) > 512 or "://" in value:
        raise ValueError("existing Production control-reader secret is invalid")
    return build_control_reader_url(runtime_url, control_writer_url, value)


def recover_control_reader_url_from_legacy_inventory(
    runtime_url: str,
    control_writer_url: str,
    legacy_read_only_url: str,
) -> str:
    value = "".join(legacy_read_only_url.strip().split())
    if not value:
        raise ValueError("legacy Production read-only URL is invalid")
    runtime = _source_coordinates(runtime_url, EXPECTED_SESSIONS["runtime"])
    writer = _source_coordinates(
        control_writer_url,
        EXPECTED_SESSIONS["control_writer"],
    )
    inventory = _source_coordinates(
        value,
        EXPECTED_CURRENT_USERS[CONTROL_READER_KIND],
    )
    if runtime != writer or runtime != inventory:
        raise ValueError("protected Production database URLs do not share one target")
    password = unquote(urlsplit(_sync_url(value)).password or "")
    return build_control_reader_url(runtime_url, control_writer_url, password)


def recover_and_prove_control_reader(
    runtime_url: str,
    control_writer_url: str,
    existing_control_reader_secret: str,
    legacy_read_only_url: str,
) -> tuple[str, dict[str, object]]:
    candidates = (
        (recover_control_reader_url, existing_control_reader_secret),
        (recover_control_reader_url_from_legacy_inventory, legacy_read_only_url),
    )
    for recover, candidate in candidates:
        if not candidate.strip():
            continue
        try:
            reader_url = recover(
                runtime_url,
                control_writer_url,
                candidate,
            )
            proof = prove_production_database_credentials(
                {
                    "runtime": runtime_url,
                    "control_writer": control_writer_url,
                    "control_reader": reader_url,
                }
            )
            return reader_url, proof
        except (ValueError, psycopg2.OperationalError):
            continue
    raise ValueError(
        "no protected Production control-reader candidate authenticated safely"
    )


def _production_targets(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def fetch_vercel_production_database_url(
    token: str,
    project_id: str,
    team_id: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> str:
    """Return the decrypted Production DATABASE_URL without persisting it."""
    if not token or not project_id or not team_id:
        raise ValueError("protected Vercel recovery coordinates are incomplete")
    endpoint = VERCEL_ENV_ENDPOINT.format(project_id=quote(project_id, safe=""))
    request = Request(
        f"{endpoint}?decrypt=true&teamId={quote(team_id, safe='')}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "vowpic-production-control-reader-repair/1",
        },
    )
    try:
        with opener(request, timeout=20) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Vercel Production environment recovery failed") from exc
    if isinstance(document, dict):
        entries = document.get("envs", document.get("environmentVariables", []))
    else:
        entries = document
    if not isinstance(entries, list):
        raise ValueError("Vercel Production environment response is invalid")
    candidates = [
        entry.get("value", "")
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("key") == "DATABASE_URL"
        and "production" in _production_targets(entry.get("target"))
        and isinstance(entry.get("value"), str)
        and entry.get("decrypted") in {True, "true", None}
    ]
    candidates = [value.strip() for value in candidates if value.strip()]
    if len(candidates) != 1:
        raise ValueError("one decrypted Production DATABASE_URL was not found")
    return candidates[0]


def _database_target(url: str) -> tuple[str, int, str]:
    parsed = urlsplit(_sync_url(url))
    host = (parsed.hostname or "").lower()
    database = parsed.path.lstrip("/")
    if (
        parsed.scheme != "postgresql"
        or not parsed.username
        or not parsed.password
        or not host
        or not database
        or parsed.port not in {5432, 6543}
        or parse_qs(parsed.query).get("sslmode") != ["require"]
        or not host.endswith(".pooler.supabase.com")
    ):
        raise ValueError("recovery database URL is not a complete Supabase URL")
    return host, int(parsed.port), database


def _validate_control_reader_role(cursor: RealDictCursor) -> None:
    cursor.execute(
        """
        SELECT role.rolcanlogin, role.rolinherit, role.rolsuper, role.rolcreatedb,
               role.rolcreaterole, role.rolreplication, role.rolbypassrls,
               COALESCE((
                   SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                   FROM pg_auth_members membership
                   JOIN pg_roles parent ON parent.oid = membership.roleid
                   WHERE membership.member = role.oid
               ), ARRAY[]::name[]) AS memberships,
               EXISTS (
                   SELECT 1 FROM pg_database database WHERE database.datdba = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_namespace namespace WHERE namespace.nspowner = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_class relation WHERE relation.relowner = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_proc routine WHERE routine.proowner = role.oid
               ) AS owns_objects
        FROM pg_roles role
        WHERE role.rolname = %s
        """,
        (CONTROL_READER_LOGIN,),
    )
    facts = cursor.fetchone()
    if facts is None:
        raise ValueError("Production control-reader login does not exist")
    forbidden = (
        not facts["rolcanlogin"],
        not facts["rolinherit"],
        facts["rolsuper"],
        facts["rolcreatedb"],
        facts["rolcreaterole"],
        facts["rolreplication"],
        facts["rolbypassrls"],
        facts["owns_objects"],
        list(facts["memberships"] or []) != [CONTROL_READER_ROLE],
    )
    if any(forbidden):
        raise ValueError("Production control-reader login violates the recovery contract")


def rotate_control_reader_password(
    admin_url: str,
    runtime_url: str,
    control_writer_url: str,
    password: str,
) -> None:
    """Use a same-target postgres authority only to rotate the fixed login."""
    runtime = _source_coordinates(runtime_url, EXPECTED_SESSIONS["runtime"])
    writer = _source_coordinates(
        control_writer_url,
        EXPECTED_SESSIONS["control_writer"],
    )
    if runtime != writer or _database_target(admin_url) != (
        runtime["host"],
        runtime["port"],
        runtime["database"],
    ):
        raise ValueError("recovery authority does not share the Production target")
    if not password or len(password) < 64 or any(character.isspace() for character in password):
        raise ValueError("Production control-reader password is invalid")
    with psycopg2.connect(
        _sync_url(admin_url),
        cursor_factory=RealDictCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_user, current_user, role.rolsuper "
                "FROM pg_roles role WHERE role.rolname = session_user"
            )
            authority = cursor.fetchone()
            if (
                authority is None
                or authority["session_user"] != "postgres"
                or authority["current_user"] != "postgres"
                or authority["rolsuper"] is not True
            ):
                raise ValueError("Vercel DATABASE_URL is not a postgres recovery authority")
            _validate_control_reader_role(cursor)
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS INHERIT PASSWORD %s VALID UNTIL 'infinity'"
                ).format(sql.Identifier(CONTROL_READER_LOGIN)),
                (password,),
            )


def prove_control_reader_after_pooler_propagation(
    runtime_url: str,
    control_writer_url: str,
    control_reader_url: str,
) -> dict[str, object]:
    urls = {
        "runtime": runtime_url,
        "control_writer": control_writer_url,
        "control_reader": control_reader_url,
    }
    for attempt, delay in enumerate(POOLER_AUTH_RETRY_DELAYS_SECONDS):
        if delay:
            time.sleep(delay)
        try:
            return prove_production_database_credentials(urls)
        except psycopg2.OperationalError:
            if attempt + 1 == len(POOLER_AUTH_RETRY_DELAYS_SECONDS):
                raise
    raise AssertionError("unreachable pooler propagation retry boundary")


def recover_prove_or_rotate_control_reader(
    runtime_url: str,
    control_writer_url: str,
    existing_control_reader_secret: str,
    legacy_read_only_url: str,
    *,
    vercel_token: str,
    vercel_project_id: str,
    vercel_team_id: str,
) -> tuple[str, dict[str, object]]:
    try:
        return recover_and_prove_control_reader(
            runtime_url,
            control_writer_url,
            existing_control_reader_secret,
            legacy_read_only_url,
        )
    except ValueError:
        reader_url = recover_control_reader_url(
            runtime_url,
            control_writer_url,
            existing_control_reader_secret,
        )
        password = unquote(urlsplit(_sync_url(reader_url)).password or "")
        admin_url = fetch_vercel_production_database_url(
            vercel_token,
            vercel_project_id,
            vercel_team_id,
        )
        rotate_control_reader_password(
            admin_url,
            runtime_url,
            control_writer_url,
            password,
        )
        proof = prove_control_reader_after_pooler_propagation(
            runtime_url,
            control_writer_url,
            reader_url,
        )
        return reader_url, proof


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
    parser.add_argument("--runtime-database-url-env", required=True)
    parser.add_argument("--control-writer-database-url-env", required=True)
    parser.add_argument("--existing-control-reader-secret-env", required=True)
    parser.add_argument("--legacy-read-only-secret-env", required=True)
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


def _optional_environment(name: str) -> str:
    import os

    return os.environ.get(name, "").strip()


def sanitized_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    return "database operation failed"


def write_failure_proof(path: Path, exc: Exception) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": FAILURE_SCHEMA,
                "state": "FAILED",
                "reason": sanitized_failure_reason(exc),
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _parse_args()
    runtime_url = _required_environment(args.runtime_database_url_env)
    writer_url = _required_environment(args.control_writer_database_url_env)
    existing_reader_secret = _required_environment(
        args.existing_control_reader_secret_env
    )
    legacy_read_only_secret = _optional_environment(args.legacy_read_only_secret_env)
    try:
        reader_url, proof = recover_prove_or_rotate_control_reader(
            runtime_url,
            writer_url,
            existing_reader_secret,
            legacy_read_only_secret,
            vercel_token=_optional_environment("VERCEL_TOKEN"),
            vercel_project_id=_optional_environment("VERCEL_PROJECT_ID"),
            vercel_team_id=_optional_environment("VERCEL_ORG_ID"),
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
    except (ValueError, psycopg2.Error) as exc:
        write_failure_proof(args.proof_output, exc)
        print(f"ERROR: {sanitized_failure_reason(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
