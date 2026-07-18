#!/usr/bin/env python3
"""Provision and publish the two non-owner Production application logins."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


RELEASE_DIR = Path(__file__).resolve().parent
ROOT = RELEASE_DIR.parents[1]
DEFAULT_CONTRACT = ROOT / "release" / "safe-baseline-contract.json"
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

import vercel_production_database_env as vercel_env  # noqa: E402
from production_database_login_proof import (  # noqa: E402
    ALLOWED_RUNTIME_PRIVILEGES,
    CONTROL_PLANE_TABLES,
    IDENTITY_SERVICE_GROUP,
    IDENTITY_TABLE_PRIVILEGES,
    RUNTIME_GROUP,
    RUNTIME_LOGIN,
    RUNTIME_SCHEMA_READINESS_PRIVILEGES,
    WRITER_GROUP,
    WRITER_LOGIN,
    prove_database_logins,
)


POOLER_AUTH_RETRY_DELAYS_SECONDS = (0, 15, 30, 60)


def load_database_role_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, tuple[str, ...]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("safe-baseline database role contract is unreadable") from exc
    roles = document.get("database_roles")
    if not isinstance(roles, dict):
        raise ValueError("safe-baseline database role contract is missing")
    fixed_names = {
        "runtime_group": RUNTIME_GROUP,
        "runtime_login": RUNTIME_LOGIN,
        "control_writer_group": WRITER_GROUP,
        "control_writer_login": WRITER_LOGIN,
    }
    for key, expected in fixed_names.items():
        if roles.get(key) != expected:
            raise ValueError(f"safe-baseline database role contract has an invalid {key}")
    raw_privileges = roles.get("runtime_business_privileges")
    if not isinstance(raw_privileges, dict) or not raw_privileges:
        raise ValueError("runtime business privilege contract is empty")
    normalized: dict[str, tuple[str, ...]] = {}
    for table, privileges in raw_privileges.items():
        if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError("runtime business privilege contract has an invalid table")
        if not isinstance(privileges, list) or not privileges:
            raise ValueError(f"runtime business privilege contract is empty for {table}")
        verbs = tuple(str(privilege).upper() for privilege in privileges)
        if len(set(verbs)) != len(verbs) or not set(verbs) <= ALLOWED_RUNTIME_PRIVILEGES:
            raise ValueError(f"runtime business privilege contract is unsafe for {table}")
        normalized[table] = verbs
    forbidden = roles.get("runtime_forbidden_business_privileges")
    if forbidden != ["DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]:
        raise ValueError("runtime forbidden business privilege contract is not exact")
    raw_schema_privileges = roles.get("runtime_schema_readiness_privileges")
    expected_schema_privileges = {
        table: list(privileges)
        for table, privileges in RUNTIME_SCHEMA_READINESS_PRIVILEGES.items()
    }
    if raw_schema_privileges != expected_schema_privileges:
        raise ValueError("runtime schema-readiness privilege contract is not exact")
    return normalized


def configure_safe_baseline_database_roles(
    cursor: RealDictCursor,
    privileges: dict[str, tuple[str, ...]],
) -> None:
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (list(privileges),),
    )
    existing = {str(row["tablename"]) for row in cursor.fetchall()}
    missing = set(privileges) - existing
    if missing:
        raise ValueError(f"safe-baseline business tables are missing: {', '.join(sorted(missing))}")
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (list(RUNTIME_SCHEMA_READINESS_PRIVILEGES),),
    )
    schema_tables = {str(row["tablename"]) for row in cursor.fetchall()}
    missing_schema_tables = set(RUNTIME_SCHEMA_READINESS_PRIVILEGES) - schema_tables
    if missing_schema_tables:
        raise ValueError(
            "safe-baseline schema-readiness tables are missing: "
            + ", ".join(sorted(missing_schema_tables))
        )
    cursor.execute(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
    )
    direct_supabase_roles = tuple(str(row["rolname"]) for row in cursor.fetchall())
    for table, verbs in privileges.items():
        relation = sql.Identifier("public", table)
        cursor.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(relation))
        cursor.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC").format(relation))
        for role in (RUNTIME_GROUP, WRITER_GROUP, *direct_supabase_roles):
            cursor.execute(
                sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                    relation,
                    sql.Identifier(role),
                )
            )
        cursor.execute(
            sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                sql.SQL(", ").join(sql.SQL(verb) for verb in verbs),
                relation,
                sql.Identifier(RUNTIME_GROUP),
            )
        )
        cursor.execute(
            sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(
                sql.Identifier(f"{table}_vowpic_runtime_service"),
                relation,
            )
        )
        for command in ALLOWED_RUNTIME_PRIVILEGES:
            cursor.execute(
                sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(
                    sql.Identifier(f"{table}_vowpic_runtime_{command.lower()}"),
                    relation,
                )
            )
        for command in verbs:
            policy = sql.Identifier(f"{table}_vowpic_runtime_{command.lower()}")
            if command == "SELECT":
                statement = "CREATE POLICY {} ON {} FOR SELECT TO {} USING (true)"
            elif command == "INSERT":
                statement = "CREATE POLICY {} ON {} FOR INSERT TO {} WITH CHECK (true)"
            elif command == "UPDATE":
                statement = (
                    "CREATE POLICY {} ON {} FOR UPDATE TO {} "
                    "USING (true) WITH CHECK (true)"
                )
            else:  # pragma: no cover - contract validation rejects this first.
                raise ValueError(f"unsupported runtime business privilege: {command}")
            cursor.execute(
                sql.SQL(statement).format(
                    policy,
                    relation,
                    sql.Identifier(RUNTIME_GROUP),
                )
            )
    for table, verbs in RUNTIME_SCHEMA_READINESS_PRIVILEGES.items():
        relation = sql.Identifier("public", table)
        cursor.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC").format(relation))
        for role in (RUNTIME_GROUP, WRITER_GROUP, *direct_supabase_roles):
            cursor.execute(
                sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                    relation,
                    sql.Identifier(role),
                )
            )
        cursor.execute(
            sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                sql.SQL(", ").join(sql.SQL(verb) for verb in verbs),
                relation,
                sql.Identifier(RUNTIME_GROUP),
            )
        )


def _sync_database_url(value: str) -> str:
    return value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)


def database_url_for_login(database_url: str, login: str, password: str) -> str:
    parsed = urlsplit(_sync_database_url(database_url))
    source_user = unquote(parsed.username or "")
    if not source_user or not parsed.hostname or not parsed.path.lstrip("/"):
        raise ValueError("migration database URL is not a complete PostgreSQL URL")
    suffix = ""
    if parsed.hostname.endswith(".pooler.supabase.com") and "." in source_user:
        suffix = "." + source_user.split(".", 1)[1]
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    userinfo = f"{quote(login + suffix, safe='')}:{quote(password, safe='')}"
    return urlunsplit((parsed.scheme, f"{userinfo}@{host}", parsed.path, parsed.query, ""))


def _is_pooler_password_propagation_failure(
    exc: psycopg2.OperationalError,
    role_urls: tuple[str, str],
) -> bool:
    hosts = tuple((urlsplit(url).hostname or "").lower() for url in role_urls)
    sqlstate = str(getattr(getattr(exc, "diag", None), "sqlstate", "") or "")
    return (
        all(host.endswith(".pooler.supabase.com") for host in hosts)
        and (
            sqlstate == "28P01"
            or "password authentication failed" in str(exc).lower()
        )
    )


def prove_database_logins_after_pooler_propagation(
    runtime_url: str,
    writer_url: str,
    business_privileges: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    """Retry only the known bounded Supavisor password-propagation boundary."""
    role_urls = (runtime_url, writer_url)
    for attempt, delay in enumerate(POOLER_AUTH_RETRY_DELAYS_SECONDS):
        if delay:
            time.sleep(delay)
        try:
            return prove_database_logins(
                runtime_url,
                writer_url,
                business_privileges,
            )
        except psycopg2.OperationalError as exc:
            is_last_attempt = attempt + 1 == len(POOLER_AUTH_RETRY_DELAYS_SECONDS)
            if is_last_attempt or not _is_pooler_password_propagation_failure(
                exc,
                role_urls,
            ):
                raise
    raise AssertionError("unreachable pooler propagation retry boundary")


def _role_facts(cursor: RealDictCursor, role_name: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT role.rolname, role.rolcanlogin, role.rolinherit, role.rolsuper, role.rolcreatedb,
               role.rolcreaterole, role.rolreplication, role.rolbypassrls,
               COALESCE((
                   SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                   FROM pg_auth_members membership
                   JOIN pg_roles parent ON parent.oid = membership.roleid
                   WHERE membership.member = role.oid
               ), ARRAY[]::name[]) AS memberships,
               EXISTS (
                   SELECT 1 FROM pg_database database
                   WHERE database.datdba = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_namespace namespace
                   WHERE namespace.nspowner = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_class relation
                   WHERE relation.relowner = role.oid
               ) OR EXISTS (
                   SELECT 1 FROM pg_proc routine
                   WHERE routine.proowner = role.oid
               ) AS owns_objects
        FROM pg_roles role
        WHERE role.rolname = %s
        """,
        (role_name,),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _validate_group(cursor: RealDictCursor, group: str) -> None:
    facts = _role_facts(cursor, group)
    if facts is None:
        raise ValueError(f"required group role {group} does not exist")
    forbidden = (
        facts["rolcanlogin"],
        facts["rolsuper"],
        facts["rolcreatedb"],
        facts["rolcreaterole"],
        facts["rolreplication"],
        facts["rolbypassrls"],
    )
    if any(forbidden) or facts["memberships"] or facts["owns_objects"]:
        raise ValueError(f"required group role {group} violates the NOLOGIN/NOBYPASSRLS contract")


def _provision_login(
    cursor: RealDictCursor,
    *,
    login: str,
    password: str,
    required_groups: tuple[str, ...],
) -> None:
    facts = _role_facts(cursor, login)
    if facts is not None:
        memberships = set(facts["memberships"] or [])
        if memberships - set(required_groups):
            raise ValueError(f"existing login {login} has an unexpected direct membership")
        if facts["owns_objects"]:
            raise ValueError(f"existing login {login} owns database objects")
    else:
        cursor.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS INHERIT"
            ).format(sql.Identifier(login))
        )
    cursor.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS INHERIT PASSWORD %s VALID UNTIL 'infinity'"
        ).format(sql.Identifier(login)),
        (password,),
    )
    for required_group in required_groups:
        cursor.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(required_group),
                sql.Identifier(login),
            )
        )


def _revoke_direct_login_privileges(
    cursor: RealDictCursor,
    *,
    login: str,
    business_tables: tuple[str, ...],
) -> None:
    cursor.execute(
        sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(login))
    )
    for table in (
        *business_tables,
        *CONTROL_PLANE_TABLES,
        *RUNTIME_SCHEMA_READINESS_PRIVILEGES,
        *IDENTITY_TABLE_PRIVILEGES,
    ):
        cursor.execute(
            "SELECT to_regclass(%s) AS relation",
            (f"public.{table}",),
        )
        relation = cursor.fetchone()
        if relation is None or relation["relation"] is None:
            continue
        cursor.execute(
            sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                sql.Identifier("public", table),
                sql.Identifier(login),
            )
        )


def provision_database_logins(
    database_url: str,
    *,
    contract_path: Path = DEFAULT_CONTRACT,
) -> tuple[str, str, dict[str, Any]]:
    runtime_password = secrets.token_urlsafe(48)
    writer_password = secrets.token_urlsafe(48)
    normalized = _sync_database_url(database_url)
    business_privileges = load_database_role_contract(contract_path)
    with psycopg2.connect(normalized, cursor_factory=RealDictCursor) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_user, current_user, current_database(), "
                "role.rolsuper AS is_superuser "
                "FROM pg_roles role WHERE role.rolname = session_user"
            )
            authority = dict(cursor.fetchone())
            if authority["current_user"] in {RUNTIME_LOGIN, WRITER_LOGIN, RUNTIME_GROUP, WRITER_GROUP}:
                raise ValueError("migration connection is not an independent provisioning authority")
            _validate_group(cursor, RUNTIME_GROUP)
            _validate_group(cursor, WRITER_GROUP)
            _validate_group(cursor, IDENTITY_SERVICE_GROUP)
            configure_safe_baseline_database_roles(cursor, business_privileges)
            cursor.execute(
                "SELECT to_regprocedure("
                "'public.vowpic_rotate_application_database_logins(text,text)')"
            )
            rotation_function = cursor.fetchone()["to_regprocedure"]
            if (
                rotation_function is not None
                and authority["session_user"] == "vowpic_migration_login"
            ):
                cursor.execute(
                    "SELECT public.vowpic_rotate_application_database_logins(%s, %s)",
                    (runtime_password, writer_password),
                )
                credential_rotation = "scoped_security_definer"
            elif authority["is_superuser"]:
                _provision_login(
                    cursor,
                    login=RUNTIME_LOGIN,
                    password=runtime_password,
                    required_groups=(RUNTIME_GROUP, IDENTITY_SERVICE_GROUP),
                )
                _provision_login(
                    cursor,
                    login=WRITER_LOGIN,
                    password=writer_password,
                    required_groups=(WRITER_GROUP,),
                )
                credential_rotation = "superuser_test_fallback"
            else:
                raise ValueError("scoped application database login rotation is missing")
            for login in (RUNTIME_LOGIN, WRITER_LOGIN):
                _revoke_direct_login_privileges(
                    cursor,
                    login=login,
                    business_tables=tuple(business_privileges),
                )
    runtime_url = database_url_for_login(normalized, RUNTIME_LOGIN, runtime_password)
    writer_url = database_url_for_login(normalized, WRITER_LOGIN, writer_password)
    proof = {
        "database": authority["current_database"],
        "credential_rotation": credential_rotation,
        "roles": prove_database_logins_after_pooler_propagation(
            runtime_url,
            writer_url,
            business_privileges,
        ),
    }
    return runtime_url, writer_url, proof


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PRODUCTION_MIGRATION_DATABASE_URL")
    parser.add_argument("--vercel-token-env", default="VERCEL_TOKEN")
    parser.add_argument("--vercel-project-id-env", default="VERCEL_PROJECT_ID")
    parser.add_argument("--vercel-team-id-env", default="VERCEL_ORG_ID")
    parser.add_argument("--cleanup-token-env", default="CLEANUP_CRON_TOKEN")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--vercel-cli", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    migration_url = os.environ.get(args.database_url_env, "").strip()
    token = os.environ.get(args.vercel_token_env, "").strip()
    project_id = os.environ.get(args.vercel_project_id_env, "").strip()
    team_id = os.environ.get(args.vercel_team_id_env, "").strip()
    cleanup_cron_token = os.environ.get(args.cleanup_token_env, "").strip()
    if not all((migration_url, token, project_id, team_id, cleanup_cron_token)):
        print("ERROR: protected database and Vercel inputs are required", file=sys.stderr)
        return 1
    runtime_url = writer_url = ""
    try:
        runtime_url, writer_url, proof = provision_database_logins(
            migration_url,
            contract_path=Path(args.contract),
        )
        vercel = vercel_env.publish_vercel_database_urls(
            vercel_cli=args.vercel_cli,
            token=token,
            project_id=project_id,
            team_id=team_id,
            runtime_url=runtime_url,
            writer_url=writer_url,
            cleanup_cron_token=cleanup_cron_token,
        )
        report = {
            "state": "PROVISIONED",
            "database": proof["database"],
            "roles": proof["roles"],
            "vercel": vercel,
        }
        _write_create_once(Path(args.output), report)
        print(json.dumps({"state": "PROVISIONED", "roles": sorted(proof["roles"])}))
        return 0
    except (ValueError, OSError, psycopg2.Error, json.JSONDecodeError) as exc:
        detail = str(exc)
        for secret in (migration_url, token, runtime_url, writer_url, cleanup_cron_token):
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
