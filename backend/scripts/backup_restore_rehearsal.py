#!/usr/bin/env python3
"""Run a disposable, cleanup-enforced PostgreSQL backup/restore rehearsal."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
import uuid


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.production_inventory_policy import inventory_policy_proof_sql  # noqa: E402
from app.services.production_inventory_service import validate_read_only_proof  # noqa: E402


NOT_RUN_EXIT = 3
MAX_REMOTE_CREDENTIAL_TTL = timedelta(hours=2)
MAX_RESTORE_POLICY_ROLES = 64


class RehearsalError(RuntimeError):
    pass


class CommandExecutionError(RehearsalError):
    pass


class ComparisonMismatchError(RehearsalError):
    pass


class CleanupFailureError(RehearsalError):
    pass


class DatabaseConnection:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        sslmode: str,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.sslmode = sslmode

    @classmethod
    def from_url(cls, value: str) -> "DatabaseConnection":
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise ValueError("database URL must use postgresql://")
        database = unquote(parsed.path.lstrip("/"))
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        host = parsed.hostname or ""
        query = parse_qs(parsed.query)
        sslmode = str((query.get("sslmode") or [""])[0]).strip().lower()
        if not all((database, username, password, host)):
            raise ValueError("database URL requires host, database, username, and password")
        return cls(
            host=host,
            port=int(parsed.port or 5432),
            database=database,
            username=username,
            password=password,
            sslmode=sslmode,
        )

    @property
    def database_identity(self) -> tuple[str, int, str]:
        return self.host.lower(), self.port, self.database.lower()

    @property
    def is_local(self) -> bool:
        return self.host.lower() in {"localhost", "127.0.0.1", "::1"}

    @property
    def is_network_isolated(self) -> bool:
        if self.is_local:
            return True
        try:
            return ipaddress.ip_address(self.host).is_private
        except ValueError:
            lowered = self.host.lower()
            return lowered.endswith((".internal", ".private", ".local"))

    def connect_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.username,
            "password": self.password,
            "connect_timeout": 10,
            **({"sslmode": self.sslmode} if self.sslmode else {}),
        }


class RehearsalConfig:
    def __init__(
        self,
        *,
        source: DatabaseConnection,
        target: DatabaseConnection,
        target_admin: DatabaseConnection,
        target_role_name: str,
        artifact_dir: Path,
        scratch_dir: Path,
        expected_target_db_prefix: str,
        target_credential_expires_at: datetime | None,
        pg_dump_executable: str = "pg_dump",
        pg_restore_executable: str = "pg_restore",
    ) -> None:
        self.source = source
        self.target = target
        self.target_admin = target_admin
        self.target_role_name = target_role_name
        self.artifact_dir = artifact_dir
        self.scratch_dir = scratch_dir
        self.expected_target_db_prefix = expected_target_db_prefix
        self.target_credential_expires_at = target_credential_expires_at
        self.pg_dump_executable = pg_dump_executable
        self.pg_restore_executable = pg_restore_executable

    @classmethod
    def from_urls(
        cls,
        *,
        source_url: str,
        target_url: str,
        target_admin_url: str,
        target_role_name: str,
        artifact_dir: Path,
        scratch_dir: Path,
        expected_target_db_prefix: str = "vowpic_restore_",
        target_credential_expires_at: datetime | None = None,
        pg_dump_executable: str = "pg_dump",
        pg_restore_executable: str = "pg_restore",
        now: datetime | None = None,
    ) -> "RehearsalConfig":
        source = DatabaseConnection.from_url(source_url)
        target = DatabaseConnection.from_url(target_url)
        target_admin = DatabaseConnection.from_url(target_admin_url)
        clean_role = target_role_name.strip()
        clean_prefix = expected_target_db_prefix.strip()
        if source.database_identity == target.database_identity:
            raise ValueError("source and rehearsal target must be different database identities")
        if not clean_prefix or not target.database.startswith(clean_prefix):
            raise ValueError("rehearsal target database has an invalid disposable prefix")
        if not clean_role.startswith("vowpic_restore_") or clean_role != target.username:
            raise ValueError("target role must be a dedicated vowpic_restore_ role matching the target URL")
        if target_admin.database_identity == target.database_identity:
            raise ValueError("target Admin connection must use a separate control database")
        if target_admin.username == target.username:
            raise ValueError("restore and target Admin credentials must be separate")
        if (target_admin.host.lower(), target_admin.port) != (target.host.lower(), target.port):
            raise ValueError("target and target Admin connections must address the same isolated server")
        for label, connection in (("source", source), ("target", target), ("target Admin", target_admin)):
            if not connection.is_local and connection.sslmode not in {"require", "verify-ca", "verify-full"}:
                raise ValueError(f"nonlocal {label} connection requires encrypted PostgreSQL transport")
        if not target.is_network_isolated:
            raise ValueError("rehearsal target must be loopback, private-addressed, or an internal hostname")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not target.is_local:
            if target_credential_expires_at is None or target_credential_expires_at.tzinfo is None:
                raise ValueError("nonlocal target requires an explicit short-lived credential expiry")
            ttl = target_credential_expires_at.astimezone(timezone.utc) - current
            if ttl <= timedelta(0) or ttl > MAX_REMOTE_CREDENTIAL_TTL:
                raise ValueError("nonlocal target credential must expire within two hours")
        resolved_artifact_dir = artifact_dir.resolve()
        resolved_scratch_dir = scratch_dir.resolve()
        if resolved_scratch_dir == resolved_artifact_dir or resolved_scratch_dir.is_relative_to(
            resolved_artifact_dir
        ):
            raise ValueError("raw backup scratch directory must be outside the artifact directory")
        return cls(
            source=source,
            target=target,
            target_admin=target_admin,
            target_role_name=clean_role,
            artifact_dir=resolved_artifact_dir,
            scratch_dir=resolved_scratch_dir,
            expected_target_db_prefix=clean_prefix,
            target_credential_expires_at=target_credential_expires_at,
            pg_dump_executable=pg_dump_executable,
            pg_restore_executable=pg_restore_executable,
        )

    @property
    def archive_path(self) -> Path:
        return self.scratch_dir / "restore.dump"

    @property
    def report_path(self) -> Path:
        return self.artifact_dir / "restore-summary.json"


def target_admin_database_connection(config: RehearsalConfig) -> DatabaseConnection:
    """Use the isolated admin on the disposable database, never on Production."""
    return DatabaseConnection(
        host=config.target_admin.host,
        port=config.target_admin.port,
        database=config.target.database,
        username=config.target_admin.username,
        password=config.target_admin.password,
        sslmode=config.target_admin.sslmode,
    )


def resolve_private_target_addresses(
    connection: DatabaseConnection,
    *,
    resolver=socket.getaddrinfo,
) -> set[str]:
    """Resolve the target now and reject any public or malformed address."""
    try:
        results = resolver(connection.host, connection.port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RehearsalError("rehearsal target DNS resolution failed") from exc
    addresses = {str(result[4][0]).strip() for result in results if result[4]}
    if not addresses:
        raise RehearsalError("rehearsal target DNS returned no addresses")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RehearsalError("rehearsal target DNS returned an invalid address") from exc
        if not (parsed.is_private or parsed.is_loopback):
            raise RehearsalError("rehearsal target resolved outside private or loopback space")
    return addresses


def _aware_datetime(value: Any, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise RehearsalError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise RehearsalError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def normalize_postgres_server_address(value: Any) -> str:
    """Return a bare IP address for PostgreSQL inet values such as 127.0.0.1/32."""
    try:
        return str(ipaddress.ip_interface(str(value or "").strip()).ip)
    except ValueError as exc:
        raise RehearsalError("connected target returned an invalid server address") from exc


def validate_target_control_proof(
    proof: dict[str, Any],
    *,
    expected_database: str,
    expected_role: str,
    expected_expires_at: datetime | None,
    resolved_addresses: set[str],
    now: datetime,
    local_target: bool,
) -> dict[str, Any]:
    database = str(proof.get("database") or "")
    role = str(proof.get("role") or "")
    owner = str(proof.get("database_owner") or "")
    if database != expected_database or role != expected_role or owner != expected_role:
        raise RehearsalError("rehearsal target database/role ownership facts do not match")
    if any(
        bool(proof.get(field))
        for field in (
            "role_superuser",
            "role_create_db",
            "role_create_role",
            "role_replication",
            "role_bypass_rls",
        )
    ):
        raise RehearsalError("rehearsal target role has privileged PostgreSQL attributes")
    if int(proof.get("privileged_membership_count") or 0) != 0:
        raise RehearsalError("rehearsal target role inherits a privileged PostgreSQL role")

    server_address = normalize_postgres_server_address(proof.get("server_address"))
    admin_server_address = normalize_postgres_server_address(
        proof.get("admin_server_address") or server_address
    )
    if server_address not in resolved_addresses or admin_server_address != server_address:
        raise RehearsalError("connected target address does not match the verified DNS/server facts")
    try:
        parsed_address = ipaddress.ip_address(server_address)
    except ValueError as exc:
        raise RehearsalError("connected target returned an invalid server address") from exc
    if not (parsed_address.is_private or parsed_address.is_loopback):
        raise RehearsalError("connected rehearsal target is not network-isolated")

    expiry_matches = local_target and expected_expires_at is None
    if not local_target:
        if expected_expires_at is None:
            raise RehearsalError("nonlocal target credential expiry is missing")
        expected = _aware_datetime(expected_expires_at, label="expected target credential expiry")
        actual = _aware_datetime(proof.get("role_valid_until"), label="database role credential expiry")
        current = _aware_datetime(now, label="current time")
        if actual <= current or actual - current > MAX_REMOTE_CREDENTIAL_TTL:
            raise RehearsalError("database role credential is not short-lived")
        expiry_matches = abs((actual - expected).total_seconds()) <= 1
        if not expiry_matches:
            raise RehearsalError("database role expiry does not match the protected input")

    return {
        "database_owner_matches": True,
        "credential_expiry_matches": expiry_matches,
        "network_isolated": True,
        "resolved_address_count": len(resolved_addresses),
        "server_address_sha256": hashlib.sha256(server_address.encode("utf-8")).hexdigest(),
        "role_nobypassrls": True,
        "role_unprivileged": True,
    }


def verify_target_controls(config: RehearsalConfig) -> dict[str, Any]:
    """Read target network, owner, membership, and credential facts from PostgreSQL."""
    import psycopg2

    resolved_addresses = resolve_private_target_addresses(config.target)
    with psycopg2.connect(**config.target.connect_kwargs()) as target_connection:
        with target_connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, host(inet_server_addr())"
            )
            database, role, server_address = cursor.fetchone()
    with psycopg2.connect(**config.target_admin.connect_kwargs()) as admin_connection:
        with admin_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_get_userbyid(database.datdba), role.rolvaliduntil,
                       role.rolsuper, role.rolcreatedb, role.rolcreaterole,
                       role.rolreplication, role.rolbypassrls,
                       (
                         SELECT count(*)
                         FROM pg_auth_members AS membership
                         JOIN pg_roles AS parent ON parent.oid = membership.roleid
                         WHERE membership.member = role.oid
                           AND (
                             parent.rolsuper OR parent.rolcreatedb OR parent.rolcreaterole
                             OR parent.rolreplication OR parent.rolbypassrls
                           )
                       ),
                       host(inet_server_addr())
                FROM pg_database AS database
                JOIN pg_roles AS role ON role.rolname = %s
                WHERE database.datname = %s
                """,
                (config.target_role_name, config.target.database),
            )
            row = cursor.fetchone()
    if row is None:
        raise RehearsalError("rehearsal target database or role does not exist")
    proof = {
        "database": str(database),
        "database_owner": str(row[0]),
        "role": str(role),
        "role_valid_until": row[1],
        "role_superuser": bool(row[2]),
        "role_create_db": bool(row[3]),
        "role_create_role": bool(row[4]),
        "role_replication": bool(row[5]),
        "role_bypass_rls": bool(row[6]),
        "privileged_membership_count": int(row[7]),
        "server_address": str(server_address),
        "admin_server_address": str(row[8]),
    }
    return validate_target_control_proof(
        proof,
        expected_database=config.target.database,
        expected_role=config.target_role_name,
        expected_expires_at=config.target_credential_expires_at,
        resolved_addresses=resolved_addresses,
        now=datetime.now(timezone.utc),
        local_target=config.target.is_local,
    )


class CommandInvocation:
    def __init__(self, argv: list[str], env: dict[str, str], *, timeout_seconds: int = 1800) -> None:
        self.argv = argv
        self.env = env
        self.timeout_seconds = timeout_seconds

    def redacted(self) -> str:
        return " ".join(shlex.quote(argument) for argument in self.argv)


def _pg_env(password: str, sslmode: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = password
    if sslmode:
        env["PGSSLMODE"] = sslmode
    return env


def build_pg_dump_invocation(config: RehearsalConfig, archive_path: Path) -> CommandInvocation:
    source = config.source
    return CommandInvocation(
        [
            config.pg_dump_executable,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--enable-row-security",
            "--schema=public",
            "--file",
            str(archive_path),
            "--host",
            source.host,
            "--port",
            str(source.port),
            "--username",
            source.username,
            "--dbname",
            source.database,
        ],
        _pg_env(source.password, source.sslmode),
    )


def build_pg_restore_invocation(config: RehearsalConfig, archive_path: Path) -> CommandInvocation:
    target = target_admin_database_connection(config)
    return CommandInvocation(
        [
            config.pg_restore_executable,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            "--host",
            target.host,
            "--port",
            str(target.port),
            "--username",
            target.username,
            "--dbname",
            target.database,
            str(archive_path),
        ],
        _pg_env(target.password, target.sslmode),
    )


def run_invocation(invocation: CommandInvocation) -> None:
    completed = subprocess.run(
        invocation.argv,
        env=invocation.env,
        capture_output=True,
        text=True,
        timeout=invocation.timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed")[-1000:]
        password = invocation.env.get("PGPASSWORD", "")
        if password:
            detail = detail.replace(password, "[REDACTED]")
        raise CommandExecutionError(
            f"database tool exited {completed.returncode}: {invocation.redacted()}; {detail.strip()}"
        )


def _source_write_probe(cursor) -> str:
    cursor.execute("SAVEPOINT vowpic_source_write_probe")
    try:
        cursor.execute("UPDATE alembic_version SET version_num = version_num WHERE false")
    except Exception as exc:
        sqlstate = str(getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None) or "unknown")
        cursor.execute("ROLLBACK TO SAVEPOINT vowpic_source_write_probe")
        cursor.execute("RELEASE SAVEPOINT vowpic_source_write_probe")
        return sqlstate
    cursor.execute("ROLLBACK TO SAVEPOINT vowpic_source_write_probe")
    cursor.execute("RELEASE SAVEPOINT vowpic_source_write_probe")
    return "write_probe_succeeded"


def verify_source_read_only(config: RehearsalConfig) -> dict[str, bool | int | str]:
    import psycopg2

    with psycopg2.connect(**config.source.connect_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            transaction_read_only = cursor.fetchone()[0] == "on"
            cursor.execute("SHOW default_transaction_read_only")
            default_transaction_read_only = cursor.fetchone()[0] == "on"
            cursor.execute(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = current_user
                """
            )
            role = cursor.fetchone()
            cursor.execute(inventory_policy_proof_sql())
            policy_columns = [str(column[0]) for column in cursor.description]
            policy_proof = dict(zip(policy_columns, cursor.fetchone(), strict=True))
            cursor.execute(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND (
                    has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'INSERT')
                    OR has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'UPDATE')
                    OR has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'DELETE')
                  )
                """
            )
            writable_table_count = int(cursor.fetchone()[0])
            write_probe_sqlstate = _source_write_probe(cursor)
        connection.rollback()
    proof: dict[str, bool | int | str] = {
        **policy_proof,
        "transaction_read_only": transaction_read_only,
        "default_transaction_read_only": default_transaction_read_only,
        "role_superuser": bool(role[0]),
        "role_create_db": bool(role[1]),
        "role_create_role": bool(role[2]),
        "role_replication": bool(role[3]),
        "role_bypass_rls": bool(role[4]),
        "writable_table_count": writable_table_count,
        "write_probe_sqlstate": write_probe_sqlstate,
    }
    validate_read_only_proof(proof)
    return proof


def prepare_restore_policy_roles(config: RehearsalConfig) -> tuple[str, ...]:
    """Create only missing NOLOGIN placeholders needed by restored RLS policies."""
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(**config.source.connect_kwargs()) as source_connection:
        source_connection.set_session(readonly=True)
        with source_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT role.rolname
                FROM pg_policy policy
                JOIN pg_class class ON class.oid = policy.polrelid
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                CROSS JOIN LATERAL unnest(policy.polroles) policy_role(oid)
                JOIN pg_roles role ON role.oid = policy_role.oid
                WHERE namespace.nspname = 'public'
                ORDER BY role.rolname
                """
            )
            policy_roles = tuple(str(row[0]) for row in cursor.fetchall())
    if len(policy_roles) > MAX_RESTORE_POLICY_ROLES:
        raise RehearsalError("source has too many role-bound public RLS policies")
    if any(not role or len(role.encode("utf-8")) > 63 or "\x00" in role for role in policy_roles):
        raise RehearsalError("source returned an invalid RLS policy role name")

    created: list[str] = []
    with psycopg2.connect(**config.target_admin.connect_kwargs()) as admin_connection:
        with admin_connection.cursor() as cursor:
            for role_name in policy_roles:
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
                if cursor.fetchone() is not None:
                    continue
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(role_name))
                )
                created.append(role_name)
    return tuple(created)


def _table_names(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        return [row[0] for row in cursor.fetchall()]


def _table_columns(connection) -> dict[str, set[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )
        columns: dict[str, set[str]] = {}
        for table_name, column_name in cursor.fetchall():
            columns.setdefault(str(table_name), set()).add(str(column_name))
        return columns


def _row_counts(connection, tables: list[str]) -> dict[str, int]:
    from psycopg2 import sql

    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table)))
            counts[table] = int(cursor.fetchone()[0])
    return counts


def _foreign_key_orphans(connection) -> int:
    from psycopg2 import sql

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT child.relname, parent.relname,
                   array_agg(child_attr.attname ORDER BY child_key.ordinality),
                   array_agg(parent_attr.attname ORDER BY child_key.ordinality)
            FROM pg_constraint constraint_row
            JOIN pg_class child ON child.oid = constraint_row.conrelid
            JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace AND child_ns.nspname = 'public'
            JOIN pg_class parent ON parent.oid = constraint_row.confrelid
            JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY child_key(attnum, ordinality) ON true
            JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY parent_key(attnum, ordinality)
              ON parent_key.ordinality = child_key.ordinality
            JOIN pg_attribute child_attr ON child_attr.attrelid = child.oid AND child_attr.attnum = child_key.attnum
            JOIN pg_attribute parent_attr ON parent_attr.attrelid = parent.oid AND parent_attr.attnum = parent_key.attnum
            WHERE constraint_row.contype = 'f'
            GROUP BY constraint_row.oid, child.relname, parent.relname
            ORDER BY child.relname, parent.relname
            """
        )
        constraints = cursor.fetchall()
        total = 0
        for child, parent, child_columns, parent_columns in constraints:
            join_terms = [
                sql.SQL("child.{} = parent.{}").format(sql.Identifier(child_col), sql.Identifier(parent_col))
                for child_col, parent_col in zip(child_columns, parent_columns)
            ]
            non_null_terms = [
                sql.SQL("child.{} IS NOT NULL").format(sql.Identifier(child_col))
                for child_col in child_columns
            ]
            query = sql.SQL(
                "SELECT count(*) FROM {} child LEFT JOIN {} parent ON {} WHERE {} AND parent.{} IS NULL"
            ).format(
                sql.Identifier("public", child),
                sql.Identifier("public", parent),
                sql.SQL(" AND ").join(join_terms),
                sql.SQL(" AND ").join(non_null_terms),
                sql.Identifier(parent_columns[0]),
            )
            cursor.execute(query)
            total += int(cursor.fetchone()[0])
        return total


def _ledger_mismatch_users(connection, tables: set[str]) -> int:
    if not {"user_credits", "credit_transactions"} <= tables:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH ledger AS (
              SELECT user_id, COALESCE(sum(amount), 0)::bigint AS amount
              FROM credit_transactions GROUP BY user_id
            ), balances AS (
              SELECT user_id, balance::bigint AS balance FROM user_credits
            )
            SELECT count(*) FROM balances FULL OUTER JOIN ledger USING (user_id)
            WHERE COALESCE(balances.balance, 0) <> COALESCE(ledger.amount, 0)
            """
        )
        return int(cursor.fetchone()[0])


def _walk_strings(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def _normalized_reference(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return value.split("?", 1)[0].split("#", 1)[0]


def build_scalar_reference_queries(
    schema_columns: dict[str, set[str]],
) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    live_portrait_columns = [
        column
        for column in ("source_image_url", "video_url")
        if column in schema_columns.get("live_portrait_jobs", set())
    ]
    if live_portrait_columns:
        queries.append(
            ("live_portrait", f"SELECT {', '.join(live_portrait_columns)} FROM live_portrait_jobs")
        )
    if "avatar_url" in schema_columns.get("users", set()):
        queries.append(("user_avatar", "SELECT avatar_url FROM users"))
    if "checkout_url" in schema_columns.get("credit_purchases", set()):
        queries.append(("credit_checkout", "SELECT checkout_url FROM credit_purchases"))
    return queries


def _url_inventory_checksum(
    connection,
    tables: set[str],
    schema_columns: dict[str, set[str]],
) -> str:
    reference_hashes: list[str] = []
    order_reference_columns = [
        column
        for column in ("source_image_urls", "preview_image_urls", "final_image_urls")
        if column in schema_columns.get("orders", set())
    ]
    if "orders" in tables and order_reference_columns:
        cursor = connection.cursor(name=f"vowpic_order_refs_{uuid.uuid4().hex}")
        try:
            cursor.itersize = 500
            cursor.execute(f"SELECT {', '.join(order_reference_columns)} FROM orders")
            for row in cursor:
                for payload in row:
                    if payload is None:
                        continue
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            pass
                    for reference in _walk_strings(payload):
                        normalized = _normalized_reference(reference)
                        reference_hashes.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        finally:
            cursor.close()
    for source_kind, statement in build_scalar_reference_queries(schema_columns):
        cursor = connection.cursor(name=f"vowpic_{source_kind}_refs_{uuid.uuid4().hex}")
        try:
            cursor.itersize = 500
            cursor.execute(statement)
            for row in cursor:
                for reference in row:
                    if reference:
                        normalized = _normalized_reference(str(reference))
                        reference_hashes.append(
                            hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                        )
        finally:
            cursor.close()
    canonical = json.dumps(sorted(reference_hashes), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _database_snapshot(connection_spec: DatabaseConnection) -> dict[str, Any]:
    import psycopg2

    with psycopg2.connect(**connection_spec.connect_kwargs()) as connection:
        connection.set_session(readonly=True)
        tables = _table_names(connection)
        table_set = set(tables)
        schema_columns = _table_columns(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = str(cursor.fetchone()[0])
        row_counts = _row_counts(connection, tables)
        return {
            "schema_revision": revision,
            "tables": tables,
            "row_counts": row_counts,
            "fk_orphans": _foreign_key_orphans(connection),
            "ledger_mismatch_users": _ledger_mismatch_users(connection, table_set),
            "url_inventory_sha256": _url_inventory_checksum(
                connection,
                table_set,
                schema_columns,
            ),
        }


def compare_source_and_target(config: RehearsalConfig) -> dict[str, Any]:
    source = _database_snapshot(config.source)
    target = _database_snapshot(target_admin_database_connection(config))
    row_counts_payload = json.dumps(source["row_counts"], sort_keys=True, separators=(",", ":"))
    comparable_keys = (
        "schema_revision",
        "tables",
        "row_counts",
        "fk_orphans",
        "ledger_mismatch_users",
        "url_inventory_sha256",
    )
    matches = all(source[key] == target[key] for key in comparable_keys)
    return {
        "matches": matches,
        "schema_revision": source["schema_revision"],
        "table_count": len(source["tables"]),
        "row_counts": source["row_counts"],
        "row_counts_sha256": hashlib.sha256(row_counts_payload.encode("utf-8")).hexdigest(),
        "url_inventory_sha256": source["url_inventory_sha256"],
        "fk_orphans": source["fk_orphans"],
        "ledger_mismatch_users": source["ledger_mismatch_users"],
    }


def cleanup_target(
    config: RehearsalConfig,
    *,
    created_policy_roles: tuple[str, ...] = (),
) -> dict[str, bool | int]:
    import psycopg2
    from psycopg2 import sql

    if not config.target.database.startswith(config.expected_target_db_prefix):
        raise CleanupFailureError("refusing cleanup for a database outside the disposable prefix")
    if not config.target_role_name.startswith("vowpic_restore_"):
        raise CleanupFailureError("refusing cleanup for a non-disposable role")
    connection = psycopg2.connect(**config.target_admin.connect_kwargs())
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (config.target.database,),
            )
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s AND pid <> pg_backend_pid()",
                (config.target_role_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(config.target.database)))
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(config.target_role_name)))
            for role_name in reversed(created_policy_roles):
                cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.target.database,))
            database_exists = cursor.fetchone() is not None
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (config.target_role_name,))
            role_exists = cursor.fetchone() is not None
            cursor.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
                (list(created_policy_roles),),
            )
            policy_role_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    if database_exists or role_exists or policy_role_count:
        raise CleanupFailureError("disposable database or role still exists after cleanup")
    return {
        "database_dropped": True,
        "role_dropped": True,
        "policy_placeholder_roles_dropped": len(created_policy_roles),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(payload)


def run_backup_restore_rehearsal(config: RehearsalConfig) -> dict[str, Any]:
    primary_error: Exception | None = None
    report: dict[str, Any] | None = None
    cleanup_result: dict[str, bool | int] | None = None
    created_policy_roles: tuple[str, ...] = ()
    try:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        config.scratch_dir.mkdir(parents=True, exist_ok=True)
        if config.archive_path.exists() or config.report_path.exists():
            raise RehearsalError("rehearsal archive/report path already exists")
        target_controls = verify_target_controls(config)
        source_proof = verify_source_read_only(config)
        created_policy_roles = prepare_restore_policy_roles(config)
        target_controls = {
            **target_controls,
            "policy_placeholder_role_count": len(created_policy_roles),
            "policy_placeholder_roles_sha256": hashlib.sha256(
                json.dumps(created_policy_roles, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        run_invocation(build_pg_dump_invocation(config, config.archive_path))
        if not config.archive_path.exists() or config.archive_path.stat().st_size <= 0:
            raise CommandExecutionError("pg_dump did not create a non-empty archive")
        archive_sha256 = _file_sha256(config.archive_path)
        run_invocation(build_pg_restore_invocation(config, config.archive_path))
        comparison = compare_source_and_target(config)
        if not comparison.get("matches"):
            raise ComparisonMismatchError("restored database does not match the read-only source snapshot")
        report = {
            "passed": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "archive_sha256": archive_sha256,
            "source_read_only": source_proof,
            "target_controls": target_controls,
            "comparison": comparison,
        }
    except Exception as exc:
        primary_error = exc
    finally:
        try:
            result = cleanup_target(config, created_policy_roles=created_policy_roles)
            cleanup_result = result if isinstance(result, dict) else {
                "database_dropped": True,
                "role_dropped": True,
                "policy_placeholder_roles_dropped": len(created_policy_roles),
            }
        except Exception as cleanup_exc:
            config.archive_path.unlink(missing_ok=True)
            raise CleanupFailureError("mandatory target database/role cleanup failed") from cleanup_exc
        config.archive_path.unlink(missing_ok=True)

    if primary_error is not None:
        if isinstance(primary_error, RehearsalError):
            raise primary_error
        raise RehearsalError("backup/restore rehearsal failed") from primary_error
    assert report is not None and cleanup_result is not None
    report["cleanup"] = cleanup_result
    _write_report(config.report_path, report)
    return report


def _required_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _parse_expiry(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("credential expiry must be timezone-aware")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url-env", required=True)
    parser.add_argument("--target-url-env", required=True)
    parser.add_argument("--target-admin-url-env", required=True)
    parser.add_argument("--target-role-name-env", required=True)
    parser.add_argument("--target-credential-expires-at-env")
    parser.add_argument("--expected-target-db-prefix", default="vowpic_restore_")
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--scratch-dir", required=True)
    parser.add_argument("--pg-dump", default="pg_dump")
    parser.add_argument("--pg-restore", default="pg_restore")
    args = parser.parse_args()
    source_url = _required_env(args.source_url_env)
    target_url = _required_env(args.target_url_env)
    admin_url = _required_env(args.target_admin_url_env)
    target_role = _required_env(args.target_role_name_env)
    if not all((source_url, target_url, admin_url, target_role)):
        print("NOT_RUN: protected source/target/Admin credentials are required", file=sys.stderr)
        return NOT_RUN_EXIT
    expiry_value = _required_env(args.target_credential_expires_at_env) if args.target_credential_expires_at_env else ""
    try:
        config = RehearsalConfig.from_urls(
            source_url=source_url,
            target_url=target_url,
            target_admin_url=admin_url,
            target_role_name=target_role,
            artifact_dir=Path(args.artifact_dir),
            scratch_dir=Path(args.scratch_dir),
            expected_target_db_prefix=args.expected_target_db_prefix,
            target_credential_expires_at=_parse_expiry(expiry_value),
            pg_dump_executable=args.pg_dump,
            pg_restore_executable=args.pg_restore,
        )
        report = run_backup_restore_rehearsal(config)
    except (ValueError, RehearsalError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": report["passed"], "report": str(config.report_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
