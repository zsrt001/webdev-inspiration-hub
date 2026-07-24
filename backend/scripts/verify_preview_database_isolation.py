"""Prove Preview database isolation and least-privilege identities read-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor


SCHEMA = "vowpic.preview-database-isolation-proof.v1"
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")
ENVIRONMENTS = {
    "migration": "PREVIEW_MIGRATION_DATABASE_URL",
    "runtime": "PREVIEW_RUNTIME_DATABASE_URL",
    "control_writer": "PREVIEW_CONTROL_PLANE_DATABASE_URL",
    "control_reader": "PREVIEW_CONTROL_READ_DATABASE_URL",
}
EXPECTED_SESSIONS = {
    "migration": "vowpic_migration_login",
    "runtime": "vowpic_release_runtime_login",
    "control_writer": "vowpic_release_control_login",
    "control_reader": "vowpic_inventory_login",
}
EXPECTED_CURRENT_USERS = {
    "migration": "vowpic_migration_owner",
    "runtime": "vowpic_app_runtime",
    "control_writer": "vowpic_control_writer_login",
    "control_reader": "vowpic_inventory_login",
}


def _sync_url(value: str) -> str:
    return value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)


def _project_ref_from_database_url(
    value: str,
    *,
    expected_login: str,
) -> tuple[str, dict[str, Any]]:
    parsed = urlsplit(_sync_url(value))
    username = unquote(parsed.username or "")
    database = parsed.path.lstrip("/")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "postgresql" or not username or not host or not database:
        raise ValueError("Preview database URL is incomplete")
    if parse_qs(parsed.query).get("sslmode") != ["require"]:
        raise ValueError("Preview database URL must require TLS")

    project_ref = ""
    connection_kind = ""
    if host.endswith(".pooler.supabase.com"):
        if parsed.port not in {5432, 6543}:
            raise ValueError("Preview Supabase pooler port is invalid")
        login, separator, project_ref = username.partition(".")
        if login != expected_login or separator != ".":
            raise ValueError("Preview pooler database login is invalid")
        connection_kind = "pooler"
    else:
        match = re.fullmatch(r"db\.([a-z0-9]{20})\.supabase\.co", host)
        if not match or parsed.port != 5432:
            raise ValueError("Preview database URL is not a Supabase endpoint")
        if username != expected_login:
            raise ValueError("Preview direct database login is invalid")
        project_ref = match.group(1)
        connection_kind = "direct"

    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise ValueError("Preview database project ref is invalid")
    return project_ref, {
        "connection_kind": connection_kind,
        "database": database,
        "login": expected_login,
        "port": int(parsed.port),
    }


def _project_ref_from_supabase_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    match = re.fullmatch(
        r"([a-z0-9]{20})\.supabase\.co",
        (parsed.hostname or "").lower(),
    )
    if parsed.scheme != "https" or not match:
        raise ValueError("Production Supabase URL is invalid")
    return match.group(1)


def validate_preview_database_urls(
    urls: dict[str, str],
    *,
    expected_preview_project_ref: str,
    production_supabase_url: str,
) -> dict[str, Any]:
    if set(urls) != set(ENVIRONMENTS):
        raise ValueError("Preview database credential URL set is invalid")
    expected_ref = expected_preview_project_ref.strip().lower()
    if not PROJECT_REF_PATTERN.fullmatch(expected_ref):
        raise ValueError("Expected Preview Supabase project ref is invalid")
    production_ref = _project_ref_from_supabase_url(production_supabase_url)
    if expected_ref == production_ref:
        raise ValueError("Preview and Production must use different Supabase projects")

    parsed_urls: dict[str, dict[str, Any]] = {}
    project_refs: set[str] = set()
    databases: set[str] = set()
    logins: set[str] = set()
    for kind, expected_login in EXPECTED_SESSIONS.items():
        project_ref, parsed = _project_ref_from_database_url(
            urls[kind],
            expected_login=expected_login,
        )
        parsed_urls[kind] = parsed
        project_refs.add(project_ref)
        databases.add(str(parsed["database"]))
        logins.add(str(parsed["login"]))
    if project_refs != {expected_ref}:
        raise ValueError("Preview database URLs do not target the declared project")
    if len(databases) != 1:
        raise ValueError("Preview database URLs must target one physical database")
    if len(logins) != len(EXPECTED_SESSIONS):
        raise ValueError("Preview database URLs must use distinct scoped logins")
    return {
        "preview_project_ref_sha256": hashlib.sha256(expected_ref.encode()).hexdigest(),
        "production_project_ref_sha256": hashlib.sha256(
            production_ref.encode()
        ).hexdigest(),
        "database": databases.pop(),
        "credentials": parsed_urls,
    }


def _connection_facts(value: str) -> dict[str, Any]:
    with psycopg2.connect(
        _sync_url(value),
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        application_name="vowpic-preview-database-isolation-proof",
    ) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_user,
                       current_user,
                       current_database() AS database,
                       current_setting('default_transaction_read_only') AS default_read_only,
                       role.rolcanlogin AS session_can_login,
                       role.rolsuper AS session_superuser,
                       role.rolcreatedb AS session_create_db,
                       role.rolcreaterole AS session_create_role,
                       role.rolreplication AS session_replication,
                       role.rolbypassrls AS session_bypass_rls,
                       inet_server_addr()::text AS server_address
                FROM pg_roles AS role
                WHERE role.rolname = session_user
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Preview database session identity is missing")
            return dict(row)


def validate_connection_facts(
    facts_by_kind: dict[str, dict[str, Any]],
    *,
    expected_database: str,
) -> dict[str, Any]:
    if set(facts_by_kind) != set(EXPECTED_SESSIONS):
        raise ValueError("Preview database connection fact set is invalid")
    server_addresses: set[str] = set()
    for kind in EXPECTED_SESSIONS:
        facts = facts_by_kind[kind]
        if (
            facts.get("session_user") != EXPECTED_SESSIONS[kind]
            or facts.get("current_user") != EXPECTED_CURRENT_USERS[kind]
            or facts.get("database") != expected_database
            or facts.get("session_can_login") is not True
            or facts.get("session_superuser") is not False
            or facts.get("session_create_db") is not False
            or facts.get("session_create_role") is not False
            or facts.get("session_replication") is not False
            or facts.get("session_bypass_rls") is not False
        ):
            raise ValueError(
                f"Preview {kind} database credential violates least privilege"
            )
        server_address = str(facts.get("server_address") or "")
        if not server_address:
            raise ValueError("Preview database physical server proof is missing")
        server_addresses.add(server_address)
    if len(server_addresses) != 1:
        raise ValueError("Preview credentials do not reach one physical database")
    if (
        str(facts_by_kind["control_reader"].get("default_read_only") or "").lower()
        != "on"
    ):
        raise ValueError("Preview control-reader credential is not read-only")
    return {
        "server_address_sha256": hashlib.sha256(
            server_addresses.pop().encode()
        ).hexdigest(),
        "sessions": {
            kind: {
                "session_user": EXPECTED_SESSIONS[kind],
                "current_user": EXPECTED_CURRENT_USERS[kind],
                "default_read_only": str(
                    facts_by_kind[kind].get("default_read_only") or ""
                ).lower(),
            }
            for kind in EXPECTED_SESSIONS
        },
    }


def prove_preview_database_isolation(
    urls: dict[str, str],
    *,
    expected_preview_project_ref: str,
    production_supabase_url: str,
) -> dict[str, Any]:
    url_proof = validate_preview_database_urls(
        urls,
        expected_preview_project_ref=expected_preview_project_ref,
        production_supabase_url=production_supabase_url,
    )
    connection_proof = validate_connection_facts(
        {kind: _connection_facts(value) for kind, value in urls.items()},
        expected_database=str(url_proof["database"]),
    )
    return {
        "schema": SCHEMA,
        "passed": True,
        **url_proof,
        **connection_proof,
    }


def _redact_error(message: str, values: list[str]) -> str:
    redacted = message
    for value in values:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return re.sub(r"postgres(?:ql)?://\S+", "[REDACTED]", redacted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    urls = {
        kind: os.environ.get(name, "").strip()
        for kind, name in ENVIRONMENTS.items()
    }
    preview_ref = os.environ.get("SUPABASE_PROJECT_REF", "").strip()
    production_url = os.environ.get("PRODUCTION_SUPABASE_URL", "").strip()
    protected_values = [*urls.values(), preview_ref, production_url]
    if any(not value for value in protected_values):
        print("ERROR: Preview isolation input is missing", file=sys.stderr)
        return 1
    try:
        proof = prove_preview_database_isolation(
            urls,
            expected_preview_project_ref=preview_ref,
            production_supabase_url=production_url,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"schema": SCHEMA, "passed": True}))
        return 0
    except (ValueError, OSError, psycopg2.Error) as exc:
        print(
            f"ERROR: {_redact_error(str(exc), protected_values)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
