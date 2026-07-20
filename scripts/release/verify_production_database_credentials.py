"""Prove protected Production database credentials without mutating data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor


SCHEMA = "vowpic.production-database-credential-proof.v1"
EXPECTED_SESSIONS = {
    "runtime": "vowpic_release_runtime_login",
    "control_writer": "vowpic_release_control_login",
    "control_reader": "vowpic_release_inventory_login",
}
EXPECTED_CURRENT_USERS = {
    "runtime": "vowpic_app_runtime",
    "control_writer": "vowpic_control_writer_login",
    "control_reader": "vowpic_inventory_login",
}
ENVIRONMENTS = {
    "runtime": "PRODUCTION_RUNTIME_DATABASE_URL",
    "control_writer": "PRODUCTION_CONTROL_PLANE_DATABASE_URL",
    "control_reader": "PRODUCTION_CONTROL_READ_DATABASE_URL",
}


def _sync_url(value: str) -> str:
    return value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)


def validate_database_urls(urls: dict[str, str]) -> dict[str, dict[str, Any]]:
    if set(urls) != set(EXPECTED_SESSIONS):
        raise ValueError("Production database credential URL set is invalid")
    parsed_urls: dict[str, dict[str, Any]] = {}
    targets: set[tuple[str, int, str]] = set()
    usernames: set[str] = set()
    for kind, expected_session in EXPECTED_SESSIONS.items():
        parsed = urlsplit(_sync_url(urls[kind]))
        username = unquote(parsed.username or "")
        database = parsed.path.lstrip("/")
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "postgresql" or not username or not host or not database:
            raise ValueError(f"{kind} Production database URL is incomplete")
        if not host.endswith(".pooler.supabase.com"):
            raise ValueError(f"{kind} Production database URL is not a Supabase pooler")
        if parsed.port not in {5432, 6543}:
            raise ValueError(f"{kind} Production database URL has an invalid port")
        if parse_qs(parsed.query).get("sslmode") != ["require"]:
            raise ValueError(f"{kind} Production database URL must require TLS")
        login = username.split(".", 1)[0]
        if login != expected_session:
            raise ValueError(f"{kind} Production database URL has an invalid login")
        usernames.add(username)
        targets.add((host, int(parsed.port), database))
        parsed_urls[kind] = {
            "login": login,
            "pooler_port": int(parsed.port),
            "database": database,
        }
    if len(usernames) != len(EXPECTED_SESSIONS):
        raise ValueError("Production database URLs must use distinct logins")
    if len(targets) != 1:
        raise ValueError("Production database URLs must target one database")
    return parsed_urls


def _connection_facts(url: str) -> dict[str, Any]:
    with psycopg2.connect(
        _sync_url(url),
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        application_name="vowpic-production-database-credential-proof",
    ) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT session_user,
                       current_user,
                       current_database() AS database,
                       current_setting('default_transaction_read_only') AS default_read_only,
                       session_role.rolcanlogin AS session_can_login,
                       session_role.rolinherit AS session_inherits,
                       session_role.rolsuper AS session_superuser,
                       session_role.rolcreatedb AS session_create_db,
                       session_role.rolcreaterole AS session_create_role,
                       session_role.rolreplication AS session_replication,
                       session_role.rolbypassrls AS session_bypass_rls,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = session_role.oid
                       ), ARRAY[]::name[]) AS session_direct_memberships,
                       COALESCE((
                           SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                           FROM pg_auth_members membership
                           JOIN pg_roles parent ON parent.oid = membership.roleid
                           WHERE membership.member = active_role.oid
                       ), ARRAY[]::name[]) AS current_direct_memberships,
                       pg_has_role(current_user, 'vowpic_runtime', 'MEMBER') AS runtime_member,
                       pg_has_role(current_user, 'vowpic_control_writer', 'MEMBER') AS control_writer_member,
                       has_table_privilege(current_user, 'public.alembic_version', 'SELECT') AS schema_select,
                       has_table_privilege(current_user, 'public.alembic_version', 'UPDATE') AS schema_update,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'SELECT') AS flags_select,
                       has_table_privilege(current_user, 'public.ops_feature_flags', 'UPDATE') AS flags_update,
                       has_table_privilege(current_user, 'public.release_activations', 'SELECT') AS activations_select,
                       has_table_privilege(current_user, 'public.release_activations', 'INSERT') AS activations_insert,
                       has_table_privilege(current_user, 'public.release_activations', 'UPDATE') AS activations_update,
                       has_table_privilege(current_user, 'public.release_activations', 'DELETE') AS activations_delete,
                       has_table_privilege(current_user, 'public.users', 'SELECT') AS users_select,
                       has_table_privilege(current_user, 'public.users', 'UPDATE') AS users_update
                FROM pg_roles session_role
                JOIN pg_roles active_role ON active_role.rolname = current_user
                WHERE session_role.rolname = session_user
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Production database credential identity is missing")
            return dict(row)


def _safe_session(facts: dict[str, Any], expected_membership: str) -> bool:
    return (
        facts.get("session_can_login") is True
        and facts.get("session_inherits") is True
        and facts.get("session_superuser") is False
        and facts.get("session_create_db") is False
        and facts.get("session_create_role") is False
        and facts.get("session_replication") is False
        and facts.get("session_bypass_rls") is False
        and set(facts.get("session_direct_memberships") or ())
        == {expected_membership}
    )


def validate_database_facts(
    facts_by_kind: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if set(facts_by_kind) != set(EXPECTED_SESSIONS):
        raise ValueError("Production database credential fact set is invalid")
    databases = {str(facts.get("database") or "") for facts in facts_by_kind.values()}
    if len(databases) != 1 or "" in databases:
        raise ValueError("Production database credentials do not target one database")

    runtime = facts_by_kind["runtime"]
    if (
        runtime.get("session_user") != EXPECTED_SESSIONS["runtime"]
        or runtime.get("current_user") != EXPECTED_CURRENT_USERS["runtime"]
        or not _safe_session(runtime, EXPECTED_CURRENT_USERS["runtime"])
        or set(runtime.get("current_direct_memberships") or ())
        != {"vowpic_identity_service", "vowpic_runtime"}
        or runtime.get("runtime_member") is not True
        or runtime.get("control_writer_member") is not False
        or runtime.get("schema_select") is not True
        or runtime.get("schema_update") is not False
        or runtime.get("flags_select") is not True
        or runtime.get("flags_update") is not False
        or runtime.get("activations_select") is not True
    ):
        raise ValueError("Production runtime credential violates least privilege")

    writer = facts_by_kind["control_writer"]
    if (
        writer.get("session_user") != EXPECTED_SESSIONS["control_writer"]
        or writer.get("current_user") != EXPECTED_CURRENT_USERS["control_writer"]
        or not _safe_session(writer, EXPECTED_CURRENT_USERS["control_writer"])
        or set(writer.get("current_direct_memberships") or ())
        != {"vowpic_control_writer"}
        or writer.get("runtime_member") is not False
        or writer.get("control_writer_member") is not True
        or writer.get("schema_update") is not False
        or writer.get("flags_select") is not True
        or writer.get("flags_update") is not True
        or writer.get("activations_select") is not True
        or writer.get("activations_insert") is not True
        or writer.get("users_update") is not False
    ):
        raise ValueError("Production control-writer credential violates least privilege")

    reader = facts_by_kind["control_reader"]
    if (
        reader.get("session_user") != EXPECTED_SESSIONS["control_reader"]
        or reader.get("current_user") != EXPECTED_CURRENT_USERS["control_reader"]
        or not _safe_session(reader, EXPECTED_CURRENT_USERS["control_reader"])
        or set(reader.get("current_direct_memberships") or ())
        or str(reader.get("default_read_only") or "").lower() != "on"
        or reader.get("runtime_member") is not False
        or reader.get("control_writer_member") is not False
        or reader.get("activations_select") is not True
        or reader.get("activations_insert") is not False
        or reader.get("activations_update") is not False
        or reader.get("activations_delete") is not False
        or reader.get("flags_update") is not False
        or reader.get("users_update") is not False
    ):
        raise ValueError("Production control-reader credential violates least privilege")

    return {
        "schema": SCHEMA,
        "passed": True,
        "database": databases.pop(),
        "credentials": {
            kind: {
                "session_user": str(facts_by_kind[kind]["session_user"]),
                "current_user": str(facts_by_kind[kind]["current_user"]),
                "default_read_only": str(
                    facts_by_kind[kind]["default_read_only"]
                ).lower(),
            }
            for kind in EXPECTED_SESSIONS
        },
    }


def prove_production_database_credentials(urls: dict[str, str]) -> dict[str, Any]:
    validate_database_urls(urls)
    return validate_database_facts(
        {kind: _connection_facts(url) for kind, url in urls.items()}
    )


def _redact_error(message: str, urls: dict[str, str]) -> str:
    redacted = message
    for value in urls.values():
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return re.sub(r"postgres(?:ql)?://\S+", "[REDACTED]", redacted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    urls = {kind: os.environ.get(name, "").strip() for kind, name in ENVIRONMENTS.items()}
    missing = [ENVIRONMENTS[kind] for kind, value in urls.items() if not value]
    if missing:
        print("ERROR: protected Production database credential is missing", file=sys.stderr)
        return 1
    try:
        proof = prove_production_database_credentials(urls)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(json.dumps({"schema": proof["schema"], "passed": True}))
        return 0
    except (ValueError, OSError, psycopg2.Error) as exc:
        print(f"ERROR: {_redact_error(str(exc), urls)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
