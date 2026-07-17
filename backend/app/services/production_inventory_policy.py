"""Exact PostgreSQL role and RLS proof for the Production inventory login."""

from __future__ import annotations

from typing import Any


INVENTORY_LOGIN = "vowpic_inventory_login"
INVENTORY_POLICY_NAME = "vowpic_inventory_select"


def validate_inventory_policy_proof(
    proof: dict[str, Any],
    *,
    require_authenticated_inventory: bool = True,
) -> None:
    if require_authenticated_inventory and proof.get("authenticated_role_name") != INVENTORY_LOGIN:
        raise ValueError("inventory source authenticated as an unexpected role")
    if proof.get("role_name") != INVENTORY_LOGIN:
        raise ValueError("inventory proof inspected an unexpected role")
    if proof.get("role_bypass_rls") is not False:
        raise ValueError("inventory source role must be NOBYPASSRLS")
    if int(proof.get("role_membership_count") or 0) != 0:
        raise ValueError("inventory source role must not inherit another role")
    if int(proof.get("owned_object_count") or 0) != 0:
        raise ValueError("inventory source role must not own database objects")
    if int(proof.get("inventory_table_count") or 0) != int(
        proof.get("readable_inventory_table_count") or 0
    ):
        raise ValueError("inventory source role cannot read every public table")
    if int(proof.get("inventory_sequence_count") or 0) != int(
        proof.get("readable_inventory_sequence_count") or 0
    ):
        raise ValueError("inventory source role cannot read every public sequence")
    if int(proof.get("rls_table_count") or 0) != int(
        proof.get("inventory_select_policy_count") or 0
    ):
        raise ValueError("inventory source role lacks exact SELECT coverage for an RLS table")
    if int(proof.get("invalid_inventory_policy_count") or 0) != 0:
        raise ValueError("inventory source role has a non-SELECT or malformed RLS policy")


def inventory_policy_proof_sql() -> str:
    return f"""
        WITH inventory_role AS (
          SELECT oid, rolname, rolbypassrls
          FROM pg_roles
          WHERE rolname = '{INVENTORY_LOGIN}'
        ), inventory_tables AS (
          SELECT class.oid, class.relrowsecurity,
                 has_table_privilege(
                   (SELECT rolname FROM inventory_role), class.oid, 'SELECT'
                 ) AS readable
          FROM pg_class class
          JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
          WHERE namespace.nspname = 'public'
            AND class.relkind IN ('r', 'p')
        ), inventory_sequences AS (
          SELECT class.oid,
                 has_sequence_privilege(
                   (SELECT rolname FROM inventory_role), class.oid, 'SELECT'
                 ) AS readable
          FROM pg_class class
          JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
          WHERE namespace.nspname = 'public'
            AND class.relkind = 'S'
        )
        SELECT
          current_user::text AS authenticated_role_name,
          role.rolname::text AS role_name,
          role.rolbypassrls AS role_bypass_rls,
          (
            SELECT count(*) FROM pg_auth_members membership
            WHERE membership.member = role.oid
          )::bigint AS role_membership_count,
          (
            (SELECT count(*) FROM pg_database database WHERE database.datdba = role.oid) +
            (SELECT count(*) FROM pg_namespace namespace WHERE namespace.nspowner = role.oid) +
            (SELECT count(*) FROM pg_class class WHERE class.relowner = role.oid) +
            (SELECT count(*) FROM pg_proc procedure WHERE procedure.proowner = role.oid)
          )::bigint AS owned_object_count,
          (SELECT count(*) FROM inventory_tables)::bigint AS inventory_table_count,
          (SELECT count(*) FROM inventory_tables WHERE readable)::bigint
            AS readable_inventory_table_count,
          (SELECT count(*) FROM inventory_sequences)::bigint AS inventory_sequence_count,
          (SELECT count(*) FROM inventory_sequences WHERE readable)::bigint
            AS readable_inventory_sequence_count,
          (SELECT count(*) FROM inventory_tables WHERE relrowsecurity)::bigint
            AS rls_table_count,
          (
            SELECT count(*)
            FROM inventory_tables table_fact
            WHERE table_fact.relrowsecurity
              AND EXISTS (
                SELECT 1 FROM pg_policy policy
                WHERE policy.polrelid = table_fact.oid
                  AND policy.polname = '{INVENTORY_POLICY_NAME}'
                  AND policy.polcmd = 'r'
                  AND policy.polpermissive
                  AND policy.polroles = ARRAY[role.oid]::oid[]
                  AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                  AND policy.polwithcheck IS NULL
              )
          )::bigint AS inventory_select_policy_count,
          (
            SELECT count(*)
            FROM pg_policy policy
            JOIN pg_class class ON class.oid = policy.polrelid
            JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
            WHERE namespace.nspname = 'public'
              AND role.oid = ANY(policy.polroles)
              AND NOT (
                class.relrowsecurity
                AND policy.polname = '{INVENTORY_POLICY_NAME}'
                AND policy.polcmd = 'r'
                AND policy.polpermissive
                AND policy.polroles = ARRAY[role.oid]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                AND policy.polwithcheck IS NULL
              )
          )::bigint AS invalid_inventory_policy_count
        FROM inventory_role role
        """
