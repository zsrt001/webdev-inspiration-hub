#!/usr/bin/env python3
"""Reconcile the dedicated full-visibility SELECT policies for Production inventory."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, text  # noqa: E402

from app.services.production_inventory_policy import (  # noqa: E402
    INVENTORY_LOGIN,
    INVENTORY_POLICY_NAME,
    inventory_policy_proof_sql,
    validate_inventory_policy_proof,
)


MIGRATION_LOGIN = "vowpic_migration_login"
MIGRATION_OWNER = "vowpic_migration_owner"


def _sync_database_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _policy_is_exact(policy: dict[str, Any], inventory_role_oid: int) -> bool:
    return (
        policy.get("command") == "r"
        and policy.get("permissive") is True
        and list(policy.get("roles") or []) == [inventory_role_oid]
        and policy.get("using_expression") == "true"
        and policy.get("check_expression") is None
    )


def reconcile_inventory_rls_policies(connection) -> dict[str, bool | int | str]:
    authority = connection.execute(
        text(
            """
            SELECT session_user::text AS session_user,
                   current_user::text AS current_user,
                   pg_has_role(session_user, 'vowpic_migration_owner', 'MEMBER') AS member
            """
        )
    ).mappings().one()
    if (
        authority["session_user"] != MIGRATION_LOGIN
        or authority["current_user"] != MIGRATION_OWNER
        or authority["member"] is not True
    ):
        raise ValueError("inventory policy reconciliation requires the exact migration login/owner")

    inventory_role = connection.execute(
        text(
            """
            SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = :role_name
            """
        ),
        {"role_name": INVENTORY_LOGIN},
    ).mappings().one_or_none()
    if inventory_role is None:
        raise ValueError("inventory login is missing")
    if (
        inventory_role["rolcanlogin"] is not True
        or any(
            inventory_role[field] is True
            for field in (
                "rolsuper",
                "rolcreatedb",
                "rolcreaterole",
                "rolreplication",
                "rolbypassrls",
            )
        )
    ):
        raise ValueError("inventory login violates the NOBYPASSRLS least-privilege contract")
    inventory_role_oid = int(inventory_role["oid"])

    table_rows = connection.execute(
        text(
            """
            SELECT namespace.nspname AS schema_name, class.relname AS table_name,
                   owner.rolname AS owner_name
            FROM pg_class class
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            JOIN pg_roles owner ON owner.oid = class.relowner
            WHERE namespace.nspname = 'public'
              AND class.relkind IN ('r', 'p')
              AND class.relrowsecurity
            ORDER BY class.oid
            """
        )
    ).mappings().all()
    if any(row["owner_name"] != MIGRATION_OWNER for row in table_rows):
        raise ValueError("every public RLS table must be owned by the migration owner")

    policies = connection.execute(
        text(
            """
            SELECT namespace.nspname AS schema_name, class.relname AS table_name,
                   policy.polname AS policy_name, policy.polcmd AS command,
                   policy.polpermissive AS permissive, policy.polroles AS roles,
                   pg_get_expr(policy.polqual, policy.polrelid) AS using_expression,
                   pg_get_expr(policy.polwithcheck, policy.polrelid) AS check_expression
            FROM pg_policy policy
            JOIN pg_class class ON class.oid = policy.polrelid
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'public'
              AND (
                policy.polname = :policy_name
                OR :inventory_role_oid = ANY(policy.polroles)
              )
            ORDER BY class.oid, policy.polname
            """
        ),
        {
            "policy_name": INVENTORY_POLICY_NAME,
            "inventory_role_oid": inventory_role_oid,
        },
    ).mappings().all()
    by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in policies:
        by_table.setdefault((row["schema_name"], row["table_name"]), []).append(dict(row))

    created = 0
    replaced = 0
    unchanged = 0
    rls_tables = {(row["schema_name"], row["table_name"]) for row in table_rows}
    for key, table_policies in by_table.items():
        schema_name, table_name = key
        for policy in table_policies:
            is_desired = (
                key in rls_tables
                and policy["policy_name"] == INVENTORY_POLICY_NAME
                and _policy_is_exact(policy, inventory_role_oid)
            )
            if is_desired:
                continue
            connection.execute(
                text(
                    f"DROP POLICY {_quote_identifier(str(policy['policy_name']))} "
                    f"ON {_quote_identifier(str(schema_name))}.{_quote_identifier(str(table_name))}"
                )
            )
            replaced += 1

    for schema_name, table_name in sorted(rls_tables):
        exact = any(
            policy["policy_name"] == INVENTORY_POLICY_NAME
            and _policy_is_exact(policy, inventory_role_oid)
            for policy in by_table.get((schema_name, table_name), [])
        )
        if exact:
            unchanged += 1
            continue
        connection.execute(
            text(
                f"CREATE POLICY {_quote_identifier(INVENTORY_POLICY_NAME)} "
                f"ON {_quote_identifier(str(schema_name))}.{_quote_identifier(str(table_name))} "
                f"AS PERMISSIVE FOR SELECT TO {_quote_identifier(INVENTORY_LOGIN)} USING (true)"
            )
        )
        created += 1

    proof = dict(connection.execute(text(inventory_policy_proof_sql())).mappings().one())
    validate_inventory_policy_proof(proof, require_authenticated_inventory=False)
    revision = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
    return {
        "schema_version": "vowpic.inventory-rls.v1",
        "schema_revision": str(revision),
        "policies_created": created,
        "policies_replaced": replaced,
        "policies_unchanged": unchanged,
        "rls_table_count": int(proof["rls_table_count"]),
        "policy_contract_complete": True,
        "role_nobypassrls": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise SystemExit(f"missing protected database URL: {args.database_url_env}")
    engine = create_engine(_sync_database_url(database_url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            report = reconcile_inventory_rls_policies(connection)
    finally:
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
