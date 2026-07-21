"""Retire two obsolete Production reader logins in one verified transaction."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


SCHEMA = "vowpic.obsolete-reader-login-cleanup.v1"
INVENTORY_LOGIN = "vowpic_inventory_login"
OBSOLETE_LOGINS = (
    "vowpic_release_control_read_login",
    "vowpic_release_inventory_login",
)
MAX_ENV_FILE_BYTES = 1024 * 1024


def _load_admin_url(vercel_env_file: Path) -> str:
    facts = vercel_env_file.lstat()
    if (
        stat.S_ISLNK(facts.st_mode)
        or not stat.S_ISREG(facts.st_mode)
        or facts.st_size <= 0
        or facts.st_size > MAX_ENV_FILE_BYTES
        or (os.name != "nt" and stat.S_IMODE(facts.st_mode) & 0o077)
    ):
        raise ValueError("protected Vercel environment file is unsafe")
    value = dotenv_values(vercel_env_file).get("DATABASE_URL")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Vercel DATABASE_URL is missing")
    return value.strip()


def _sync_url(value: str) -> str:
    return value.strip().replace("postgresql+asyncpg://", "postgresql://", 1)


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
        raise ValueError("Vercel DATABASE_URL is not a complete TLS PostgreSQL URL")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
    elif host.endswith(".pooler.supabase.com"):
        _login, separator, project_ref = username.partition(".")
        if separator != ".":
            project_ref = ""
    else:
        project_ref = ""
    if not project_ref:
        raise ValueError("Vercel DATABASE_URL is not a Supabase project URL")
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


def retire_obsolete_reader_logins(
    admin_url: str,
    expected_project_ref: str,
) -> dict[str, object]:
    project_ref, database = _database_target(admin_url)
    if project_ref != expected_project_ref:
        raise ValueError("Vercel DATABASE_URL does not target the expected project")
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


def _replace_sanitized_proof(output: Path, proof: dict[str, object]) -> None:
    if output.is_symlink() or not output.is_file():
        raise ValueError("sanitized cleanup proof target is unsafe")
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(proof, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return "private cleanup evidence could not be written"
    return "database cleanup failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vercel-env-file", required=True)
    parser.add_argument("--expected-project-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        proof = retire_obsolete_reader_logins(
            _load_admin_url(Path(args.vercel_env_file)),
            args.expected_project_ref.strip(),
        )
        _replace_sanitized_proof(Path(args.output), proof)
    except (OSError, ValueError, psycopg2.Error) as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "state": "FAILED",
                    "reason": _safe_failure_reason(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"schema": SCHEMA, "state": "PASSED"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
