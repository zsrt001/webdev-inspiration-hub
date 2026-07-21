"""Retire two obsolete Production reader logins in one verified transaction."""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler
import json
import os
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


SCHEMA = "vowpic.obsolete-reader-login-cleanup.v1"
INVENTORY_LOGIN = "vowpic_inventory_login"
OBSOLETE_LOGINS = (
    "vowpic_release_control_read_login",
    "vowpic_release_inventory_login",
)
ADMIN_DATABASE_URL_ENVS = (
    "POSTGRES_URL_NON_POOLING",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "SUPABASE_DB_URL",
    "DATABASE_URL",
)
PREFIXED_ADMIN_DATABASE_URL_ENV = re.compile(
    r"[A-Z][A-Z0-9_]{0,31}_(?:POSTGRES_URL_NON_POOLING|POSTGRES_URL|"
    r"POSTGRES_PRISMA_URL|SUPABASE_DB_URL)"
)
EXPECTED_PROJECT_REF_ENV = "EXPECTED_SUPABASE_PROJECT_REF"
TRIGGER_TOKEN_ENV = "CLEANUP_TRIGGER_TOKEN"


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise ValueError(f"required protected function variable is missing: {name}")
    return value


def _sync_url(value: str) -> str:
    normalized = value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    return normalized.replace("postgres://", "postgresql://", 1)


def _database_target(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(_sync_url(database_url))
    username = unquote(parsed.username or "")
    host = (parsed.hostname or "").lower()
    database = parsed.path.lstrip("/")
    if (
        parsed.scheme != "postgresql"
        or not username
        or not parsed.password
        or not host
        or not database
        or parsed.port not in {5432, 6543}
        or parse_qs(parsed.query).get("sslmode") != ["require"]
    ):
        raise ValueError("protected database URL is not a complete TLS PostgreSQL URL")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
    elif host.endswith(".pooler.supabase.com"):
        _login, separator, project_ref = username.partition(".")
        if separator != ".":
            project_ref = ""
    else:
        project_ref = ""
    if not project_ref:
        raise ValueError("protected database URL is not a Supabase project URL")
    return project_ref, database


def _role_facts(cursor: RealDictCursor, login: str) -> dict[str, Any] | None:
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
        (login,),
    )
    facts = cursor.fetchone()
    return dict(facts) if facts is not None else None


def _assert_role_facts(
    facts: dict[str, Any],
    *,
    login: str,
    expected_memberships: set[str],
) -> None:
    if (
        facts.get("rolcanlogin") is not True
        or facts.get("rolinherit") is not True
        or facts.get("rolsuper") is not False
        or facts.get("rolcreatedb") is not False
        or facts.get("rolcreaterole") is not False
        or facts.get("rolreplication") is not False
        or facts.get("rolbypassrls") is not False
        or facts.get("owns_objects") is not False
        or set(facts.get("memberships") or ()) != expected_memberships
    ):
        raise ValueError(f"Production login {login} violates the cleanup contract")


def _validate_recovery_authority(cursor: RealDictCursor) -> None:
    cursor.execute(
        "SELECT session_user, current_user, role.rolsuper, role.rolcreaterole "
        "FROM pg_roles role WHERE role.rolname = session_user"
    )
    authority = cursor.fetchone()
    if (
        authority is None
        or authority["session_user"] != "postgres"
        or authority["current_user"] != "postgres"
    ):
        raise ValueError("protected database URL does not use the postgres admin role")
    if authority["rolcreaterole"] is not True:
        raise ValueError("Supabase postgres admin role cannot manage database roles")


def retire_obsolete_reader_logins(
    admin_url: str,
    expected_project_ref: str,
) -> dict[str, object]:
    project_ref, database = _database_target(admin_url)
    if project_ref != expected_project_ref or database != "postgres":
        raise ValueError("protected database URL does not target the expected project")
    states: dict[str, str] = {}
    with psycopg2.connect(
        _sync_url(admin_url),
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        application_name="vowpic-obsolete-reader-login-cleanup",
    ) as connection:
        with connection.cursor() as cursor:
            _validate_recovery_authority(cursor)
            inventory = _role_facts(cursor, INVENTORY_LOGIN)
            if inventory is None:
                raise ValueError("Production inventory login does not exist")
            _assert_role_facts(
                inventory,
                login=INVENTORY_LOGIN,
                expected_memberships=set(),
            )
            for login in OBSOLETE_LOGINS:
                facts = _role_facts(cursor, login)
                if facts is None:
                    states[login] = "ALREADY_ABSENT"
                    continue
                _assert_role_facts(
                    facts,
                    login=login,
                    expected_memberships={INVENTORY_LOGIN},
                )
                states[login] = "PENDING"
            for login in OBSOLETE_LOGINS:
                if states[login] == "ALREADY_ABSENT":
                    continue
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH NOLOGIN").format(sql.Identifier(login))
                )
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(INVENTORY_LOGIN),
                        sql.Identifier(login),
                    )
                )
                cursor.execute(
                    sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(login))
                )
                cursor.execute(
                    sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ROLE").format(
                        sql.Identifier(login),
                        sql.Identifier(database),
                    )
                )
                cursor.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(login))
                )
                states[login] = "DELETED"
            for login in OBSOLETE_LOGINS:
                if _role_facts(cursor, login) is not None:
                    raise ValueError(f"obsolete Production login {login} still exists")
            inventory = _role_facts(cursor, INVENTORY_LOGIN)
            if inventory is None:
                raise ValueError("Production inventory login disappeared")
            _assert_role_facts(
                inventory,
                login=INVENTORY_LOGIN,
                expected_memberships=set(),
            )
    return {
        "schema": SCHEMA,
        "state": "PASSED",
        "database": database,
        "inventory_login": "PRESERVED",
        "obsolete_logins": states,
    }


def _probe_recovery_authority(
    database_url: str,
    expected_project_ref: str,
) -> None:
    project_ref, database = _database_target(database_url)
    if project_ref != expected_project_ref or database != "postgres":
        raise ValueError("protected database URL does not target the expected project")
    with psycopg2.connect(
        _sync_url(database_url),
        cursor_factory=RealDictCursor,
        connect_timeout=15,
        application_name="vowpic-obsolete-reader-login-authority-probe",
    ) as connection:
        with connection.cursor() as cursor:
            _validate_recovery_authority(cursor)


def retire_obsolete_reader_logins_from_environment(
    environment: Mapping[str, str],
    expected_project_ref: str,
) -> dict[str, object]:
    candidate_seen = False
    prefixed_names = sorted(
        name
        for name in environment
        if name not in ADMIN_DATABASE_URL_ENVS
        and not name.startswith(("NEXT_PUBLIC_", "PUBLIC_"))
        and PREFIXED_ADMIN_DATABASE_URL_ENV.fullmatch(name) is not None
    )
    for name in (*ADMIN_DATABASE_URL_ENVS, *prefixed_names):
        candidate = str(environment.get(name) or "").strip()
        if not candidate:
            continue
        candidate_seen = True
        try:
            _probe_recovery_authority(candidate, expected_project_ref)
        except (ValueError, psycopg2.Error):
            continue
        return retire_obsolete_reader_logins(candidate, expected_project_ref)
    if not candidate_seen:
        raise ValueError("no allowlisted protected database URL is configured")
    raise ValueError(
        "no protected Vercel database URL provides postgres role administration"
    )


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return "cleanup function could not read its protected configuration"
    return "database cleanup failed"


def handle_cleanup_request(
    authorization: str,
    environment: Mapping[str, str],
) -> tuple[int, dict[str, object]]:
    try:
        trigger_token = _required_environment(environment, TRIGGER_TOKEN_ENV)
        if re.fullmatch(r"[0-9a-f]{64}", trigger_token) is None:
            raise ValueError("cleanup function trigger contract is invalid")
    except (OSError, ValueError, psycopg2.Error) as exc:
        return 503, {
            "schema": SCHEMA,
            "state": "FAILED",
            "reason": _safe_failure_reason(exc),
        }
    if not hmac.compare_digest(authorization, f"Bearer {trigger_token}"):
        return 404, {"state": "NOT_FOUND"}
    try:
        expected_project_ref = _required_environment(
            environment,
            EXPECTED_PROJECT_REF_ENV,
        )
        if re.fullmatch(r"[a-z0-9]{20}", expected_project_ref) is None:
            raise ValueError("expected Supabase project identity is invalid")
        proof = retire_obsolete_reader_logins_from_environment(
            environment,
            expected_project_ref,
        )
    except (OSError, ValueError, psycopg2.Error) as exc:
        return 409, {
            "schema": SCHEMA,
            "state": "FAILED",
            "reason": _safe_failure_reason(exc),
        }
    return 200, proof


class handler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        status, payload = handle_cleanup_request(
            str(self.headers.get("Authorization") or ""),
            os.environ,
        )
        self._write_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._write_json(404, {"state": "NOT_FOUND"})

    def log_message(self, _format: str, *_args: object) -> None:
        return
