#!/usr/bin/env python3
"""Verify the exact post-0020 runtime database surface for COMMERCIAL_7A."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "release" / "commercial-7a-database-role-contract.json"
DEFAULT_MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260710_0020_partner_consent.py"
)
EXPECTED_SCHEMA_REVISION = "20260710_0021"
RUNTIME_GROUP = "vowpic_runtime"
RUNTIME_LOGIN = "vowpic_app_runtime"
CONTROL_WRITER_GROUP = "vowpic_control_writer"
ALLOWED_RUNTIME_PRIVILEGES = {"SELECT", "INSERT", "UPDATE"}
FORBIDDEN_RUNTIME_PRIVILEGES = {
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
}
CHECKED_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def load_commercial_database_role_contract(
    path: Path = DEFAULT_CONTRACT,
) -> tuple[dict[str, tuple[str, ...]], str]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("COMMERCIAL_7A database role contract is unreadable") from exc
    if document.get("contract_version") != "commercial-7a.database-roles.v1":
        raise ValueError("COMMERCIAL_7A database role contract version is invalid")
    if document.get("schema_revision") != EXPECTED_SCHEMA_REVISION:
        raise ValueError("COMMERCIAL_7A database role schema revision is invalid")
    if document.get("base_contract") != "release/safe-baseline-contract.json":
        raise ValueError("COMMERCIAL_7A database role base contract is invalid")
    expected_names = {
        "runtime_group": RUNTIME_GROUP,
        "runtime_login": RUNTIME_LOGIN,
        "control_writer_group": CONTROL_WRITER_GROUP,
    }
    for key, expected in expected_names.items():
        if document.get(key) != expected:
            raise ValueError(f"COMMERCIAL_7A database role {key} is invalid")
    if document.get("runtime_forbidden_business_privileges") != [
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ]:
        raise ValueError("COMMERCIAL_7A forbidden privileges are not exact")

    raw_privileges = document.get("runtime_additive_business_privileges")
    if not isinstance(raw_privileges, dict) or not raw_privileges:
        raise ValueError("COMMERCIAL_7A runtime privilege contract is empty")
    normalized: dict[str, tuple[str, ...]] = {}
    for table, privileges in raw_privileges.items():
        if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError("COMMERCIAL_7A runtime privilege table is invalid")
        if not isinstance(privileges, list) or not privileges:
            raise ValueError(f"COMMERCIAL_7A runtime privileges are empty for {table}")
        verbs = tuple(str(privilege).upper() for privilege in privileges)
        if (
            len(set(verbs)) != len(verbs)
            or not set(verbs) <= ALLOWED_RUNTIME_PRIVILEGES
        ):
            raise ValueError(
                f"COMMERCIAL_7A runtime privileges are unsafe for {table}"
            )
        normalized[table] = verbs
    return normalized, hashlib.sha256(raw).hexdigest()


def verify_contract_matches_migration(
    *,
    contract_path: Path = DEFAULT_CONTRACT,
    migration_path: Path = DEFAULT_MIGRATION,
) -> tuple[dict[str, tuple[str, ...]], str]:
    privileges, contract_sha256 = load_commercial_database_role_contract(
        contract_path
    )
    try:
        tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise ValueError("COMMERCIAL_7A database migration is unreadable") from exc
    migration_privileges: dict[str, tuple[str, ...]] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "COMMERCIAL_7A_RUNTIME_ADDITIVE_PRIVILEGES"
            for target in node.targets
        ):
            continue
        try:
            raw_value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(
                "COMMERCIAL_7A migration privilege map is not literal"
            ) from exc
        if not isinstance(raw_value, dict):
            raise ValueError("COMMERCIAL_7A migration privilege map is invalid")
        migration_privileges = {
            str(table): tuple(str(value).upper() for value in verbs)
            for table, verbs in raw_value.items()
        }
        break
    if migration_privileges is None:
        raise ValueError("COMMERCIAL_7A migration privilege map is missing")
    if migration_privileges != privileges:
        raise ValueError(
            "COMMERCIAL_7A database role contract does not match the migration"
        )
    return privileges, contract_sha256


def validate_commercial_database_role_facts(
    *,
    privileges: dict[str, tuple[str, ...]],
    table_facts: dict[str, dict[str, Any]],
    login_facts: dict[str, Any],
) -> dict[str, Any]:
    if set(table_facts) != set(privileges):
        raise ValueError("COMMERCIAL_7A database role proof has an invalid table set")
    if (
        login_facts.get("runtime_login") != RUNTIME_LOGIN
        or not login_facts.get("runtime_login_can_login")
        or not login_facts.get("runtime_login_inherits")
        or login_facts.get("runtime_login_superuser")
        or login_facts.get("runtime_login_create_db")
        or login_facts.get("runtime_login_create_role")
        or login_facts.get("runtime_login_replication")
        or login_facts.get("runtime_login_bypass_rls")
        or not login_facts.get("runtime_group_member")
        or login_facts.get("control_writer_group_member")
    ):
        raise ValueError("COMMERCIAL_7A runtime login violates least privilege")

    total_privileges = 0
    force_rls_tables = 0
    for table, expected_verbs in privileges.items():
        facts = table_facts[table]
        if not facts.get("row_security_enabled"):
            raise ValueError(f"COMMERCIAL_7A row security is disabled for {table}")
        if facts.get("force_row_security"):
            force_rls_tables += 1
        actual_runtime = {
            privilege
            for privilege in CHECKED_TABLE_PRIVILEGES
            if facts.get(f"runtime_{privilege.lower()}")
        }
        actual_writer = {
            privilege
            for privilege in CHECKED_TABLE_PRIVILEGES
            if facts.get(f"writer_{privilege.lower()}")
        }
        if actual_runtime != set(expected_verbs):
            raise ValueError(
                f"COMMERCIAL_7A runtime privileges are invalid for {table}"
            )
        if actual_writer:
            raise ValueError(
                f"COMMERCIAL_7A control writer has business privileges for {table}"
            )
        public_privileges = {
            str(value).upper() for value in facts.get("public_privileges") or ()
        }
        if public_privileges:
            raise ValueError(f"COMMERCIAL_7A PUBLIC privileges exist for {table}")
        expected_policy_names = {
            f"{table}_vowpic_runtime_{command.lower()}"
            for command in expected_verbs
        }
        actual_policy_names = set(facts.get("runtime_policy_names") or ())
        actual_policy_commands = {
            str(command).upper()
            for command in facts.get("runtime_policy_commands") or ()
        }
        if (
            actual_policy_names != expected_policy_names
            or actual_policy_commands != set(expected_verbs)
        ):
            raise ValueError(
                f"COMMERCIAL_7A runtime policies are invalid for {table}"
            )
        total_privileges += len(expected_verbs)

    return {
        "schema": "vowpic.commercial-7a-database-role-proof.v1",
        "passed": True,
        "schema_revision": EXPECTED_SCHEMA_REVISION,
        "runtime_login": RUNTIME_LOGIN,
        "runtime_group": RUNTIME_GROUP,
        "control_writer_group": CONTROL_WRITER_GROUP,
        "table_count": len(privileges),
        "runtime_privilege_count": total_privileges,
        "force_rls_table_count": force_rls_tables,
        "forbidden_runtime_privileges": sorted(FORBIDDEN_RUNTIME_PRIVILEGES),
    }


async def prove_commercial_database_roles(
    db: AsyncSession,
    *,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    privileges, contract_sha256 = verify_contract_matches_migration(
        contract_path=contract_path
    )
    login_row = (
        await db.execute(
            text(
                """
                SELECT login.rolname AS runtime_login,
                       login.rolcanlogin AS runtime_login_can_login,
                       login.rolinherit AS runtime_login_inherits,
                       login.rolsuper AS runtime_login_superuser,
                       login.rolcreatedb AS runtime_login_create_db,
                       login.rolcreaterole AS runtime_login_create_role,
                       login.rolreplication AS runtime_login_replication,
                       login.rolbypassrls AS runtime_login_bypass_rls,
                       pg_has_role(login.rolname, :runtime_group, 'MEMBER')
                         AS runtime_group_member,
                       pg_has_role(login.rolname, :writer_group, 'MEMBER')
                         AS control_writer_group_member
                FROM pg_roles login
                WHERE login.rolname = :runtime_login
                """
            ),
            {
                "runtime_group": RUNTIME_GROUP,
                "writer_group": CONTROL_WRITER_GROUP,
                "runtime_login": RUNTIME_LOGIN,
            },
        )
    ).mappings().one_or_none()
    if login_row is None:
        raise ValueError("COMMERCIAL_7A runtime login is missing")

    table_facts: dict[str, dict[str, Any]] = {}
    privilege_expressions = ",\n".join(
        (
            f"has_table_privilege(:runtime_group, 'public.' || :table, "
            f"'{privilege}') AS runtime_{privilege.lower()},\n"
            f"has_table_privilege(:writer_group, 'public.' || :table, "
            f"'{privilege}') AS writer_{privilege.lower()}"
        )
        for privilege in CHECKED_TABLE_PRIVILEGES
    )
    table_query = text(
        f"""
        SELECT relation.relrowsecurity AS row_security_enabled,
               relation.relforcerowsecurity AS force_row_security,
               {privilege_expressions},
               COALESCE((
                   SELECT array_agg(acl.privilege_type ORDER BY acl.privilege_type)
                   FROM aclexplode(
                       COALESCE(
                           relation.relacl,
                           acldefault('r', relation.relowner)
                       )
                   ) AS acl
                   WHERE acl.grantee = 0
               ), ARRAY[]::text[]) AS public_privileges,
               COALESCE((
                   SELECT array_agg(policy.policyname ORDER BY policy.policyname)
                   FROM pg_policies policy
                   WHERE policy.schemaname = 'public'
                     AND policy.tablename = :table
                     AND CAST(:runtime_group AS name) = ANY(policy.roles)
               ), ARRAY[]::text[]) AS runtime_policy_names,
               COALESCE((
                   SELECT array_agg(policy.cmd ORDER BY policy.cmd)
                   FROM pg_policies policy
                   WHERE policy.schemaname = 'public'
                     AND policy.tablename = :table
                     AND CAST(:runtime_group AS name) = ANY(policy.roles)
               ), ARRAY[]::text[]) AS runtime_policy_commands
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = :table
          AND relation.relkind IN ('r', 'p')
        """
    )
    for table in privileges:
        row = (
            await db.execute(
                table_query,
                {
                    "runtime_group": RUNTIME_GROUP,
                    "writer_group": CONTROL_WRITER_GROUP,
                    "table": table,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"COMMERCIAL_7A business table is missing: {table}")
        table_facts[table] = dict(row)

    proof = validate_commercial_database_role_facts(
        privileges=privileges,
        table_facts=table_facts,
        login_facts=dict(login_row),
    )
    proof["contract_sha256"] = contract_sha256
    return proof
